"""
AttachmentPreloadMiddleware — runs once per agent invocation, before
the model sees the user's message. If the user attached files this
turn (file ids in the runtime config), the middleware fabricates a
synthetic `read_attachment` tool call per id and injects the result
into the message history. The model "wakes up" with the file content
already in context — it doesn't have to know or guess to call the
read_attachment tool.

Part of the react_agent library base: all environmental access goes
through the ports in `react_agent.ports` (storage, extraction,
captioning) — no host imports.

Sequence the model sees:

    HumanMessage("look at this image")           ← user's actual turn
    AIMessage(tool_calls=[read_attachment(f1)])  ← synthetic
    ToolMessage(content=<f1 content>)            ← synthetic
    [now the model generates its real reply]

This unifies attachments with regular tool results so downstream
machinery — eviction, prompt caching, content-reference rendering —
needs no special-case for "the user attached something".

Why a hook on `before_agent` rather than `before_model`:

  `before_agent` fires once per agent invocation. `before_model` fires
  once per model call inside the react loop — and the same loop can
  call the model many times across multiple tool iterations. We want
  the preload exactly once per user turn, before the first model call.

Idempotency: re-injection on a resumed thread would duplicate the
synthetic messages. We guard by marking the rewritten HumanMessage with
`additional_kwargs["_preloaded_attachments"]` and skipping when the
most recent HumanMessage already carries it.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import Overwrite

from ..ports import (
    AttachmentConfig,
    AttachmentStore,
    CaptionModel,
    ContentExtractor,
    LoggerLike,
)
from ..references import category_for_mime
from ..tools.read_attachment import materialize_attachment

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

_log = logging.getLogger(__name__)


# Marker on the rewritten HumanMessage's `additional_kwargs` — lets us
# detect "we already preloaded for this user turn" and skip duplicate
# injection on a resumed run.
_PRELOAD_MARKER = "_preloaded_attachments"
_PRELOAD_TOOL_NAME = "read_attachment"


# ---- middleware --------------------------------------------------------------


class AttachmentPreloadMiddleware(AgentMiddleware):
    """Pre-loads user-attached files into the agent's message history
    as synthetic `read_attachment` tool calls before the first model
    call of the turn.

    Constructor takes the same `AttachmentStore` + `ContentExtractor`
    ports that `make_read_attachment_tool` does — they share the
    `materialize_attachment` helper so behavior matches whether the
    file enters via this middleware or via an explicit tool call.
    """

    def __init__(
        self,
        *,
        store: AttachmentStore,
        extractor: ContentExtractor,
        captioner: CaptionModel,
        config: AttachmentConfig,
        logger: LoggerLike,
        agent_accepts_image: bool,
    ) -> None:
        super().__init__()
        self._store = store
        self._extractor = extractor
        self._captioner = captioner
        self._config = config
        self._logger = logger
        # When the agent's underlying model is vision-capable, images
        # go in as multimodal content blocks (native vision). When
        # not, we OCR them via the captioner port and inject the
        # extracted text — the agent's chat model never sees the
        # bytes. Per-agent declaration, fixed at build time.
        self._agent_accepts_image = agent_accepts_image

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: "Runtime[Any]",
    ) -> dict[str, Any] | None:
        attachments, owner_id = _runtime_context(runtime)
        _log.info(
            "attachment_preload.fired attachments=%s owner_id=%s msgs=%d",
            attachments,
            owner_id,
            len(state.get("messages") or []),
        )
        if not attachments or not owner_id:
            _log.info("attachment_preload.skip reason=no_attachments_or_owner")
            return None

        messages = list(state.get("messages") or [])
        last_human_idx = _last_human_index(messages)
        if last_human_idx < 0:
            _log.info("attachment_preload.skip reason=no_human_message")
            return None
        human_msg = messages[last_human_idx]
        if _already_preloaded(human_msg):
            _log.info("attachment_preload.skip reason=already_preloaded")
            return None

        # Materialize every attachment. Split images (vision content
        # blocks) from text (extracted file content). Why: OpenAI's
        # `/chat/completions` only accepts STRING content on a
        # ToolMessage — image content blocks inside a ToolMessage are
        # not delivered as vision input. So images go INTO the
        # HumanMessage (where multimodal content blocks ARE supported)
        # and text goes through the synthetic tool-call path.
        #
        # Each attachment gets a model-facing inline alias
        # (`turn{N}{cat}{M}`, 0-indexed turn / 1-indexed item — the
        # exact grammar `react_agent.references` resolves). The alias
        # is minted from the attachment SNAPSHOT on the HumanMessage —
        # the same list the host's lookup resolves against — so the
        # alias counters can never drift from what resolution sees.
        # A file in the config list but absent from the snapshot
        # (deleted / cross-owner — the host already dropped it) gets
        # no alias and doesn't advance the counters.
        turn_index = sum(
            1 for m in messages[:last_human_idx] if isinstance(m, HumanMessage)
        )
        snapshot_by_id = _snapshot_by_id(human_msg)
        alias_counters: dict[str, int] = {}

        image_blocks: list[dict[str, Any]] = []
        text_attachments: list[tuple[str, str]] = []  # (file_id, text)
        for file_id in attachments:
            alias, filename = _alias_for(
                file_id=file_id,
                snapshot=snapshot_by_id,
                turn_index=turn_index,
                counters=alias_counters,
            )
            # Preview-sized injection: only the first
            # `config.preview_chars` of a text/PDF file goes into
            # context up front. `materialize_attachment` appends the
            # "N chars remain, call read_attachment(file_id, offset=…)"
            # footer automatically when the file is bigger than the
            # preview, so the model knows the file continues and how to
            # page through it. Images are unaffected (all-or-nothing).
            payload = await materialize_attachment(
                store=self._store,
                extractor=self._extractor,
                file_id=file_id,
                owner_id=owner_id,
                max_chars=self._config.preview_chars,
            )
            if isinstance(payload, list):
                # Image — branch on whether THIS agent's model can
                # actually see images natively.
                if self._agent_accepts_image:
                    image_blocks.extend(payload)
                    # The alias mapping is MODEL-FACING plumbing — it
                    # must never touch the HumanMessage (hosts flatten
                    # its text blocks into the user's bubble). It rides
                    # the same synthetic tool pair the text attachments
                    # use.
                    if alias:
                        text_attachments.append(
                            (
                                file_id,
                                f"[Image attachment '{filename}' — inline "
                                f"alias: {alias}. The image itself is "
                                "visible in the user's message above.]",
                            )
                        )
                else:
                    ocr_text = await self._ocr_image(
                        file_id=file_id, owner_id=owner_id
                    )
                    text_attachments.append(
                        (file_id, _with_alias_header(ocr_text, alias))
                    )
            else:
                # String — either extracted text or a tool-error string.
                text_attachments.append(
                    (file_id, _with_alias_header(payload, alias))
                )

        new_messages = list(messages)

        if image_blocks:
            new_messages[last_human_idx] = _human_with_image_blocks(
                human_msg, image_blocks
            )

        preload_pairs: list[Any] = []
        if text_attachments:
            preload_pairs = _build_tool_preload(text_attachments)
            new_messages = (
                new_messages[: last_human_idx + 1]
                + preload_pairs
                + new_messages[last_human_idx + 1 :]
            )
            if not image_blocks:
                # Text-only turn: the human message wasn't rewritten by
                # the image path, so stamp the idempotency marker here —
                # otherwise a resumed run would re-inject the pairs.
                new_messages[last_human_idx] = _with_preload_marker(
                    human_msg
                )

        if not image_blocks and not preload_pairs:
            _log.info("attachment_preload.skip reason=empty_payload")
            return None

        _log.info(
            "attachment_preload.injected images=%d text_preload_msgs=%d total_msgs=%d",
            len(image_blocks),
            len(preload_pairs),
            len(new_messages),
        )
        return {"messages": Overwrite(new_messages)}

    async def _ocr_image(self, *, file_id: str, owner_id: str) -> str:
        """Fallback when the agent's model is text-only: bytes go to
        the captioner port (a separate vision-capable call the host
        wires) and the description is injected via the synthetic
        tool-call path so the chat model sees it as a normal tool
        result.

        Best-effort — any failure returns a placeholder rather than
        raising. Better to tell the model "could not read image" than
        crash the turn."""
        try:
            view = await self._store.get(file_id=file_id, owner_id=owner_id)
            data = await self._store.read_bytes(
                file_id=file_id, owner_id=owner_id
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "preload.ocr_file_fetch_failed",
                file_id=file_id,
                error=repr(exc),
            )
            return f"[Could not read image {file_id}.]"
        description = await self._captioner.caption_image(
            data=data, mime_type=view.mime_type
        )
        if description:
            return (
                f"[Image attachment '{view.filename}' — vision-extracted "
                f"description (the chat model can't see images natively): "
                f"{description}]"
            )
        return (
            f"[Image attachment '{view.filename}' — vision call "
            f"returned nothing; image content unavailable.]"
        )


# ---- module helpers ----------------------------------------------------------


def _runtime_context(runtime: Any) -> tuple[list[str], str | None]:
    """Pull `attachments` + `owner_id` out of the active RunnableConfig.

    LangGraph's `Runtime` class doesn't expose `config` directly — the
    per-run RunnableConfig lives in a contextvar
    (`var_child_runnable_config`) that's set for the duration of the
    graph execution. We read it from there. `runtime` is kept as a
    parameter for forward-compat in case a future version adds a
    direct accessor.

    Returns `([], None)` if either piece is missing — caller treats
    that as "nothing to do" rather than crashing the turn.
    """
    del runtime  # currently unused; see docstring
    config = var_child_runnable_config.get()
    if not isinstance(config, dict):
        return [], None
    configurable = config.get("configurable") or {}
    raw = configurable.get("attachments")
    owner_id = configurable.get("owner_id")
    attachments = [a for a in raw if isinstance(a, str) and a] if isinstance(raw, list) else []
    return attachments, owner_id if isinstance(owner_id, str) else None


def _last_human_index(messages: list) -> int:
    """Index of the most recent HumanMessage in `messages`, or -1 if
    none. The preload pair gets inserted right after this position."""
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], HumanMessage):
            return idx
    return -1


def _already_preloaded(human_msg: Any) -> bool:
    """True iff this HumanMessage already carries our preload marker.
    Prevents re-injection on resumed runs where the rewritten message
    is already in the checkpoint."""
    extras = getattr(human_msg, "additional_kwargs", None) or {}
    return bool(extras.get(_PRELOAD_MARKER))


def _snapshot_by_id(human_msg: Any) -> dict[str, dict[str, Any]]:
    """Index the HumanMessage's attachment snapshot by file id. The
    snapshot (stamped by the host's runner under
    `additional_kwargs.attachments`) is what reference resolution
    walks, so minting from it guarantees both sides agree on order
    and category."""
    extras = getattr(human_msg, "additional_kwargs", None) or {}
    raw = extras.get("attachments") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, dict):
            fid = item.get("id")
            if isinstance(fid, str) and fid:
                out[fid] = item
    return out


def _alias_for(
    *,
    file_id: str,
    snapshot: dict[str, dict[str, Any]],
    turn_index: int,
    counters: dict[str, int],
) -> tuple[str | None, str]:
    """Compute the model-facing alias + filename for one attachment
    from the snapshot. A file that's not in the snapshot gets
    `(None, "")` and is left unaliased; crucially the counters DON'T
    advance for it, so they stay aligned with the snapshot the lookup
    walks."""
    meta = snapshot.get(file_id)
    if meta is None:
        return None, ""
    mime = str(meta.get("mime_type", ""))
    filename = str(meta.get("filename", ""))
    return (
        _next_alias(mime_type=mime, counters=counters, turn_index=turn_index),
        filename,
    )


def _next_alias(
    *, mime_type: str, counters: dict[str, int], turn_index: int
) -> str:
    """Mint the next alias for this turn's attachment stream. The
    `file` counter advances for EVERY attachment (the grammar's `file`
    category matches any MIME), while the specific category counter
    (`image`/`pdf`/`audio`/`video`) advances only for its own MIME —
    keeping the minted alias resolvable by the host's lookup exactly
    as written. MIME→category lives in ONE place (`category_for_mime`
    in `react_agent.references`) so mint and resolve can't drift."""
    counters["file"] = counters.get("file", 0) + 1
    category = category_for_mime(mime_type)
    if category == "file":
        return f"turn{turn_index}file{counters['file']}"
    counters[category] = counters.get(category, 0) + 1
    return f"turn{turn_index}{category}{counters[category]}"


def _with_alias_header(text: str, alias: str | None) -> str:
    """Prefix a text payload with its inline-alias header so the model
    knows the handle to use when mentioning this file in prose. No-op
    when alias minting failed."""
    if not alias:
        return text
    return f"[inline alias: {alias}]\n{text}"


def _human_with_image_blocks(
    human_msg: HumanMessage, image_blocks: list[dict[str, Any]]
) -> HumanMessage:
    """Return a new HumanMessage whose content is a list of content
    blocks: the original text (if any) plus every image block — and
    NOTHING else. Alias hints and any other model-facing plumbing ride
    the synthetic tool pair instead; hosts flatten this message's text
    blocks straight into the user's bubble, so any text we add here
    would render as the user's own words. Marks the new message with
    `_PRELOAD_MARKER` so a resumed run sees "already preloaded" and
    skips the path."""
    text = human_msg.content if isinstance(human_msg.content, str) else ""
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(image_blocks)
    extras = dict(human_msg.additional_kwargs or {})
    extras[_PRELOAD_MARKER] = True
    return HumanMessage(
        content=blocks,
        id=getattr(human_msg, "id", None),
        additional_kwargs=extras,
    )


def _with_preload_marker(human_msg: HumanMessage) -> HumanMessage:
    """Copy of the message with only the idempotency marker added —
    used by the text-only path, which doesn't rewrite content."""
    extras = dict(human_msg.additional_kwargs or {})
    extras[_PRELOAD_MARKER] = True
    return HumanMessage(
        content=human_msg.content,
        id=getattr(human_msg, "id", None),
        additional_kwargs=extras,
    )


def _build_tool_preload(
    text_attachments: list[tuple[str, str]],
) -> list[Any]:
    """Synthesize one AIMessage with N tool_calls + one ToolMessage
    per attachment for the text-bearing path. OpenAI's tool message
    format requires string content — the extracted file text fits.

    The file id is stamped on EACH ToolMessage's additional_kwargs so
    the compaction middleware can recover it later without walking
    back to the paired AIMessage."""
    tool_calls: list[dict[str, Any]] = []
    tool_messages: list[ToolMessage] = []
    for file_id, text in text_attachments:
        call_id = f"preload-{uuid.uuid4().hex[:12]}"
        tool_calls.append(
            {
                "name": _PRELOAD_TOOL_NAME,
                "id": call_id,
                "args": {"file_id": file_id},
            }
        )
        tool_messages.append(
            ToolMessage(
                content=text,
                name=_PRELOAD_TOOL_NAME,
                tool_call_id=call_id,
                additional_kwargs={"file_id": file_id},
            )
        )
    ai_msg = AIMessage(
        content="",
        tool_calls=tool_calls,
        additional_kwargs={_PRELOAD_MARKER: True},
    )
    return [ai_msg, *tool_messages]
