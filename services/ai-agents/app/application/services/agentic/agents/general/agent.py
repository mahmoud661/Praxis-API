"""
GeneralAgent — the default multi-purpose agent.

Thin by design: this file is the DI seam (constructor annotations the
container resolves) plus the capability spec. ALL assembly lives in
`graph.py`; prompts in `prompts/`; the state machine in `sections.py`;
agent-specific tools in `tools/`. The registry discovers this class by
convention: every package under `agents/` exposes its `BaseAgent`
subclass in an `agent.py` module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ......domain.IServices.i_files_service import IFilesService
from ......domain.IServices.i_knowledge_service import IKnowledgeService
from ......domain.ports.content_reference_lookup import (
    IContentReferenceLookup,
)
from ......domain.ports.document_extractor import IDocumentExtractor
from ......domain.ports.logger import Logger
from ......infrastructure.agentic.agentic_store import AgenticStore
from ......infrastructure.config.env import Env
from ......infrastructure.llm.attachment_captioner import AttachmentCaptioner
from ...agent_spec import AgentConstraints, AgentSpec
from ...base_agent import BaseAgent

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


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
        captioner: AttachmentCaptioner,
        logger: Logger,
    ) -> None:
        super().__init__()
        self._agentic = agentic_store
        self._env = env
        self._files = files_service
        self._extractor = document_extractor
        self._knowledge = knowledge_service
        self._content_ref_lookup = content_reference_lookup
        self._captioner = captioner
        self._logger = logger
        # Instance attribute shadows the class-level placeholder. The
        # registry reads `instance.spec`, so the env-derived value wins.
        self.spec = _build_spec(env.litellm_model)

    def _build(self) -> "CompiledStateGraph":
        # Deferred import — `graph.py` pulls the react_agent runtime.
        from .graph import build_graph

        return build_graph(
            spec=self.spec,
            env=self._env,
            agentic_store=self._agentic,
            files_service=self._files,
            document_extractor=self._extractor,
            captioner=self._captioner,
            knowledge_service=self._knowledge,
            content_reference_lookup=self._content_ref_lookup,
            logger=self._logger,
        )


# ---- spec builder ------------------------------------------------------------


def _build_spec(underlying_model: str) -> AgentSpec:
    """Build the spec the registry serves. Called from `__init__` with
    `env.litellm_model` so the capability declaration tracks deploy
    config without code changes.

    `read_attachment` is plumbing — always bound for agents that accept
    file uploads, NOT surfaced as a user-toggleable tool.
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
        # still bound to the agent at build time (see `graph.py`) — the
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
