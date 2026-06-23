"""
Graph assembly for the general agent — connects the agent's pieces
(prompts, sections, tools, middlewares) into one compiled react_agent
graph. This is the ONLY file in the agent package that imports the
react_agent library's runtime; everything else here is declarative.

The function takes the host services as explicit parameters (passed by
`agent.py`'s DI constructor) and maps them onto the library's ports:

    FilesService          → AttachmentStore       (structural match)
    DocumentExtractor     → ContentExtractor      (structural match)
    AttachmentCaptioner   → CaptionModel          (infrastructure adapter)
    ContentReferenceLookupService → ReferenceLookup
    Env                   → AttachmentConfig      (plain values)

Middleware order matters:
  1. attachment_preload — fires once per turn on `before_agent`,
     injects this turn's attachments (image content blocks into the
     HumanMessage, text via synthetic tool calls).
  2. attachment_compaction — fires on every `before_model` call,
     strips bytes from attachments older than `keep_turns`, replaced
     with self-describing re-fetch stubs (lazy cached captions).
  3. history_compaction — wraps every model call, builds a compacted
     "effective view" of the conversation (truncate old tool args →
     clear stale kb_search results → LLM summary at the configured
     fraction of the context window). Never writes to LangGraph state.
  4. content_references — `awrap_model_call`, scans the assistant's
     text AFTER the model returns, resolves `turn3image1` /
     `citeturn0search2` aliases to typed payloads on
     `additional_kwargs.content_references`.
  5. prompt_caching — stamps the cache breakpoint on the trailing
     message AFTER everything else has rewritten the list.
  6. section_flow — qualify → execute phase gating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# NOTE: this module imports the react_agent RUNTIME at module scope —
# heavyweight (LangGraph internals) and not importable in every dev
# environment. That's fine BECAUSE this module itself is loaded lazily:
# `agent.py` imports it inside `_build()`, the single deferred import
# for the whole agent. Nothing else may import graph.py at module scope.
#
# Addressing: the graph core + state machine are imported TOP-LEVEL
# (`react_agent.…`) because the vendored package self-imports that way —
# mixing addressings for those modules would create duplicate class
# instances. The attachment system (ports/tools/middlewares) is
# addressed through the app-relative path, matching every other
# app-side consumer.
from langchain_openai import ChatOpenAI
from react_agent.graph import create_react_agent
from react_agent.state_machine.section_flow_middleware import (
    SectionFlowMiddleware,
)

from ...react_agent.middlewares.attachment_compaction_middleware import (
    AttachmentCompactionMiddleware,
)
from ...react_agent.middlewares.attachment_preload_middleware import (
    AttachmentPreloadMiddleware,
)
from ...react_agent.middlewares.compaction_middleware import (
    CompactionConfig,
    CompactionMiddleware,
)
from ...react_agent.middlewares.content_reference_middleware import (
    ContentReferenceMiddleware,
)
from ...react_agent.middlewares.prompt_caching_middleware import (
    PromptCachingMiddleware,
)
from ...react_agent.ports import AttachmentConfig
from ...react_agent.tools import make_read_attachment_tool
from .prompts import SYSTEM_PROMPT
from .sections import INITIAL_SECTION, build_sections
from .tools import (
    make_kb_search_tool,
    make_memory_forget_tool,
    make_memory_search_tool,
    make_memory_store_tool,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from ......domain.IServices.i_files_service import IFilesService
    from ......domain.IServices.i_knowledge_service import IKnowledgeService
    from ......domain.ports.content_reference_lookup import (
        IContentReferenceLookup,
    )
    from ......domain.ports.document_extractor import IDocumentExtractor
    from ......domain.ports.i_memory_client import IMemoryClient
    from ......domain.ports.logger import Logger
    from ......infrastructure.agentic.agentic_store import AgenticStore
    from ......infrastructure.config.env import Env
    from ......infrastructure.llm.attachment_captioner import (
        AttachmentCaptioner,
    )
    from ...agent_spec import AgentSpec


def build_graph(
    *,
    spec: "AgentSpec",
    env: "Env",
    agentic_store: "AgenticStore",
    files_service: "IFilesService",
    document_extractor: "IDocumentExtractor",
    captioner: "AttachmentCaptioner",
    knowledge_service: "IKnowledgeService",
    memory_client: "IMemoryClient",
    content_reference_lookup: "IContentReferenceLookup",
    logger: "Logger",
) -> "CompiledStateGraph":
    # LiteLLM proxy is OpenAI-compatible — `ChatOpenAI` works as-is.
    # Model name comes from the spec, not env, so the capability
    # declaration and the runtime invocation can't drift.
    model = ChatOpenAI(
        model=spec.underlying_model,
        api_key=env.litellm_proxy_api_key,
        base_url=env.litellm_proxy_api_base,
    )

    # The library's attachment knobs, mapped from app env. Plain values
    # cross the boundary — the library never sees pydantic-settings.
    attachment_config = AttachmentConfig(
        preview_chars=env.attachment_preview_chars,
        page_chars=env.attachment_page_chars,
        keep_turns=env.attachment_compaction_keep_turns,
    )

    tools = [
        make_read_attachment_tool(
            store=files_service,
            extractor=document_extractor,
            lookup=content_reference_lookup,
            page_chars=attachment_config.page_chars,
        ),
        make_kb_search_tool(
            knowledge_service=knowledge_service,
            agentic_store=agentic_store,
        ),
        make_memory_search_tool(memory_client=memory_client),
        make_memory_store_tool(memory_client=memory_client),
        make_memory_forget_tool(memory_client=memory_client),
    ]

    section_flow = SectionFlowMiddleware(
        sections=build_sections(execute_tools=tools),
        initial_section=INITIAL_SECTION,
    )

    attachment_preload = AttachmentPreloadMiddleware(
        store=files_service,
        extractor=document_extractor,
        captioner=captioner,
        config=attachment_config,
        logger=logger,
        # Image attachments go straight into HumanMessage content only
        # when this agent's underlying model supports vision. Otherwise
        # the preload OCRs them via the captioner and injects the
        # description through the tool path.
        agent_accepts_image="image" in (spec.accepts_modalities or []),
    )
    attachment_compaction = AttachmentCompactionMiddleware(
        store=files_service,
        extractor=document_extractor,
        captioner=captioner,
        config=attachment_config,
        logger=logger,
    )
    # Conversation-history compaction — keeps long threads inside the
    # model's context window. Reuses the agent's own chat model for
    # the Level 4 summary call. Config choices, deliberate:
    #   - collapse disabled: it replaces tool rounds with a badge like
    #     "[Collapsed: Read 2 files]"; read_attachment results are
    #     already stubbed by attachment compaction, and collapsing
    #     kb_search rounds would destroy retrieval results the model
    #     may still be citing this turn.
    #   - microcompact only targets kb_search: stale retrieval results
    #     (>60 min) clear to a placeholder; attachment tool results
    #     stay owned by the attachment middleware.
    #   - max_input_tokens from env: the LiteLLM proxy hides the
    #     upstream model profile, so introspection can't find the
    #     context window — without the override the summarize trigger
    #     would never fire.
    history_compaction = CompactionMiddleware(
        model=model,
        config=CompactionConfig(
            trigger=("fraction", env.compaction_trigger_fraction),
            keep=("messages", env.compaction_keep_messages),
            collapse_enabled=False,
            microcompact_tools=frozenset({"kb_search"}),
        ),
        max_input_tokens=env.compaction_max_input_tokens,
    )
    content_references = ContentReferenceMiddleware(
        lookup=content_reference_lookup,
    )
    prompt_caching = PromptCachingMiddleware()

    return create_react_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            attachment_preload,
            attachment_compaction,
            history_compaction,
            content_references,
            prompt_caching,
            section_flow,
        ],
        checkpointer=agentic_store.checkpointer,
    )
