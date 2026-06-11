"""
GeneralAgent — the default multi-purpose agent.

Mounts the platform's real tools on the execute phase: `read_attachment`
(materializes uploaded files into the model's context) and `kb_search`
(retrieves relevant chunks from the user's knowledge base via Qdrant).
Both tools are constructed by their respective factories with the
agent's DI dependencies injected at build time.

Tools are constructed in `_build()` rather than at module load so each
agent instance gets a fresh tool bound to that instance's services —
no cross-instance state leakage, no module-level mutable state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from langchain_openai import ChatOpenAI

from .....domain.IServices.i_files_service import IFilesService
from .....domain.IServices.i_knowledge_service import IKnowledgeService
from .....domain.ports.content_reference_lookup import IContentReferenceLookup
from .....domain.ports.document_extractor import IDocumentExtractor
from .....domain.ports.logger import Logger
from .....infrastructure.agentic.agentic_store import AgenticStore
from .....infrastructure.config.env import Env
from ..agent_spec import (
    AgentConstraints,
    AgentSpec,
)
from ..base_agent import BaseAgent
from ..tools.kb_search import make_kb_search_tool
from ..tools.read_attachment import make_read_attachment_tool

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


# ---- prompts -----------------------------------------------------------------

_QUALIFY_PROMPT = (
    "You are in the QUALIFY phase. Ask one short clarifying "
    "question if the user's request is ambiguous. Otherwise "
    "call `change_section` with target=`execute` and proceed. "
    "Do not use other tools in this phase."
)

_EXECUTE_PROMPT = (
    "You are in the EXECUTE phase. Use the available tools "
    "to fulfil the user's request, then answer concisely. "
    "When the user attaches a file, call `read_attachment` "
    "with its id before answering. When the user asks about "
    "topics likely covered by their uploaded documents, call "
    "`kb_search` first."
)

_SYSTEM_PROMPT = (
    "You are Praxis, the platform's main agent. Be precise, "
    "concise, and never invent tool results.\n\n"
    "Inline references: every attachment you receive is labeled with "
    "an inline alias (e.g. `turn0image1`, `turn1pdf1`, `turn2file1` — "
    "shown next to the attachment's content). When your reply refers "
    "to an attached file or image, write its alias bare in the "
    "sentence, like: 'The chart in turn0image1 shows a steady rise.' "
    "The UI replaces the alias with a rich preview chip, so use it "
    "instead of the filename when pointing at the file. Only use "
    "aliases you were actually given — never invent one. To cite "
    "knowledge-base search results, use the `cite` aliases the "
    "kb_search tool provides (e.g. `citeturn0search2`)."
)


# Minimal placeholder spec — `BaseAgent.__init_subclass__` checks
# `cls.__dict__["spec"]` at class-definition time (BEFORE env is
# loaded), so we need a syntactically valid AgentSpec right here.
# The instance constructor swaps in the real one built from env, and
# the registry reads `instance.spec`, so the placeholder is never
# observed by anything except the class-definition validator.
_PLACEHOLDER_SPEC = AgentSpec(
    id="general",
    display_name="General Assistant",
    description="(placeholder — replaced per-instance in __init__)",
    underlying_model="__placeholder__",
    accepts_modalities=["text"],
    tools=[],
)


# ---- agent -------------------------------------------------------------------


class GeneralAgent(BaseAgent):
    spec: ClassVar[AgentSpec] = _PLACEHOLDER_SPEC

    def __init__(
        self,
        agentic_store: AgenticStore,
        env: Env,
        files_service: IFilesService,
        document_extractor: IDocumentExtractor,
        knowledge_service: IKnowledgeService,
        content_reference_lookup: IContentReferenceLookup,
        logger: Logger,
    ) -> None:
        super().__init__()
        self._agentic = agentic_store
        self._env = env
        self._files = files_service
        self._extractor = document_extractor
        self._knowledge = knowledge_service
        self._content_ref_lookup = content_reference_lookup
        self._logger = logger
        # Instance attribute shadows the class-level placeholder. The
        # registry reads `instance.spec`, so the env-derived value wins.
        self.spec = _build_spec(env.litellm_model)

    def _build(self) -> "CompiledStateGraph":
        # Local imports — see the comment in the legacy `MainAgent` for
        # why these are deferred. Same reasoning still applies.
        from react_agent.graph import create_react_agent
        from react_agent.state_machine.section_flow_middleware import (
            SectionFlowMiddleware,
        )
        from react_agent.state_machine.types.config_types import SectionConfig

        # Local import: `react_agent/__init__.py` eagerly pulls
        # `langgraph._internal`, which isn't on the local dev path. Keep
        # this here so any non-build caller (DI introspection, tests
        # that touch the agent class but never compile a graph) doesn't
        # pay that import.
        from ..react_agent.middlewares.attachment_compaction_middleware import (
            AttachmentCompactionMiddleware,
        )
        from ..react_agent.middlewares.attachment_preload_middleware import (
            AttachmentPreloadMiddleware,
        )
        from ..react_agent.middlewares.compaction_middleware import (
            CompactionConfig,
            CompactionMiddleware,
        )
        from ..react_agent.middlewares.content_reference_middleware import (
            ContentReferenceMiddleware,
        )
        from ..react_agent.middlewares.prompt_caching_middleware import (
            PromptCachingMiddleware,
        )

        # LiteLLM proxy is OpenAI-compatible — `ChatOpenAI` works as-is.
        # Model name comes from the spec, not env, so the capability
        # declaration and the runtime invocation can't drift.
        model = ChatOpenAI(
            model=self.spec.underlying_model,
            api_key=self._env.litellm_proxy_api_key,
            base_url=self._env.litellm_proxy_api_base,
        )

        # Tools are constructed per-build with this agent's injected
        # services captured in their closures. The factory pattern
        # avoids module-level state and keeps each tool's dependencies
        # explicit.
        tools = [
            make_read_attachment_tool(
                files=self._files,
                extractor=self._extractor,
                lookup=self._content_ref_lookup,
                page_chars=self._env.attachment_page_chars,
            ),
            make_kb_search_tool(
                knowledge_service=self._knowledge,
                agentic_store=self._agentic,
            ),
        ]

        section_flow = SectionFlowMiddleware(
            sections={
                "qualify": SectionConfig(
                    name="qualify",
                    prompt=_QUALIFY_PROMPT,
                    allowed_transitions=["execute"],
                ),
                "execute": SectionConfig(
                    name="execute",
                    prompt=_EXECUTE_PROMPT,
                    tools=[t.name for t in tools],
                    allowed_transitions=[],
                ),
            },
            initial_section="qualify",
        )

        # Middleware order matters:
        #   1. preload — fires once per turn on `before_agent`,
        #      injects this turn's attachments (image content blocks
        #      into the HumanMessage, text via synthetic tool calls).
        #   2. compaction — fires on every `before_model` call,
        #      strips bytes from attachments older than `keep_turns`
        #      and replaces them with `[Attachment cleared — was:
        #      <caption>. Re-fetch via read_attachment(id).]`. Lazy
        #      captioning via LLM on first eviction; cached on the
        #      file's metadata for subsequent evictions.
        #   3. history_compaction — wraps every model call, builds a
        #      compacted "effective view" of the conversation (truncate
        #      old tool args → clear stale kb_search results → LLM
        #      summary at 85% of the context window). Never writes to
        #      LangGraph state; the checkpointer keeps full history.
        #   4. content_references — runs on `awrap_model_call`, scans
        #      the assistant's text AFTER the model returns, resolves
        #      `turn3image1` / `citeturn0search2` aliases to typed
        #      payloads, and attaches them to
        #      `additional_kwargs.content_references` for the
        #      frontend's rich-rendering layer.
        #   5. section_flow — qualify → execute phase gating.
        attachment_preload = AttachmentPreloadMiddleware(
            files=self._files,
            extractor=self._extractor,
            env=self._env,
            logger=self._logger,
            # Image attachments go straight into HumanMessage content
            # only when this agent's underlying model supports vision.
            # Otherwise the preload middleware OCRs them via a vision
            # call and injects the description through the tool path.
            agent_accepts_image="image" in (self.spec.accepts_modalities or []),
        )
        attachment_compaction = AttachmentCompactionMiddleware(
            files=self._files,
            extractor=self._extractor,
            env=self._env,
            logger=self._logger,
        )
        # Conversation-history compaction — keeps long threads inside the
        # model's context window. Reuses the agent's own chat model for
        # the Level 4 summary call. Wraps the model call OUTSIDE
        # prompt_caching (earlier in the list = outer), so caching stamps
        # the COMPACTED message list, not the raw one.
        #
        # Config choices, deliberate:
        #   - collapse disabled: it replaces tool rounds with a badge
        #     like "[Collapsed: Read 2 files]". Our read_attachment
        #     results are already stubbed (with a re-fetch hint) by
        #     AttachmentCompactionMiddleware, and collapsing kb_search
        #     rounds would destroy retrieval results the model may
        #     still be citing this turn.
        #   - microcompact only targets kb_search: stale retrieval
        #     results (>60 min old) clear to a placeholder; attachment
        #     tool results stay owned by the attachment middleware.
        #   - max_input_tokens from env: the LiteLLM proxy hides the
        #     upstream model profile, so introspection can't find the
        #     context window — without the override the summarize
        #     trigger would never fire.
        history_compaction = CompactionMiddleware(
            model=model,
            config=CompactionConfig(
                trigger=("fraction", self._env.compaction_trigger_fraction),
                keep=("messages", self._env.compaction_keep_messages),
                collapse_enabled=False,
                microcompact_tools=frozenset({"kb_search"}),
            ),
            max_input_tokens=self._env.compaction_max_input_tokens,
        )
        content_references = ContentReferenceMiddleware(
            lookup=self._content_ref_lookup,
        )
        # Prompt caching runs LAST in the model-call wrap chain so it
        # sees the final outgoing message list AFTER everything else
        # has rewritten it. The cache breakpoint sits on the trailing
        # message — Anthropic upstreams (via LiteLLM) reuse the
        # cached prefix at ~10% cost on subsequent turns. No-op for
        # OpenAI upstreams.
        prompt_caching = PromptCachingMiddleware()

        return create_react_agent(
            model=model,
            tools=tools,
            system_prompt=_SYSTEM_PROMPT,
            middleware=[
                attachment_preload,
                attachment_compaction,
                history_compaction,
                content_references,
                prompt_caching,
                section_flow,
            ],
            checkpointer=self._agentic.checkpointer,
        )


# ---- spec builder ------------------------------------------------------------


def _build_spec(underlying_model: str) -> AgentSpec:
    """Build the spec the registry serves. Called from `__init__` with
    `env.litellm_model` so the capability declaration tracks deploy
    config without code changes.

    `read_attachment` is plumbing — always bound for agents that accept
    file uploads, NOT surfaced as a user-toggleable tool. `kb_search`
    is user-toggleable so users can opt out of RAG when they want a
    purely conversational chat.
    """
    return AgentSpec(
        id="general",
        display_name="General Assistant",
        description=(
            "Multi-purpose chat. Accepts images and PDFs; can search your "
            "uploaded knowledge base."
        ),
        icon="sparkles",
        underlying_model=underlying_model,
        accepts_modalities=["text", "image", "pdf"],
        # No user-toggleable tools surfaced for now. `kb_search` is
        # still bound to the agent at build time (see `_build`) — the
        # model can call it whenever a question looks like it'd
        # benefit from retrieval — it just isn't rendered as a chip
        # in the composer. Flip an `AgentTool(user_toggleable=True)`
        # here later if/when we want the user to opt in or out.
        tools=[],
        constraints=AgentConstraints(
            max_runtime_seconds=120,
            streams_partial_tokens=True,
        ),
        visibility="public",
    )
