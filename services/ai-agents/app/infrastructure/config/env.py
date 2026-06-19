from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "ai-agents-service"
    port: int = 8000

    database_url: str
    kafka_brokers: str  # comma-separated
    # Redis holds the per-run event buffer the AgentRunner streams into.
    # Cleared when the run ends (success or error).
    redis_url: str

    # LiteLLM proxy is the model provider — exposes an OpenAI-compatible
    # API in front of whichever upstream model is configured on the proxy.
    # The agent's `ChatOpenAI` client points at `litellm_proxy_api_base` with
    # `litellm_proxy_api_key`, and asks for `litellm_model` by name.
    litellm_proxy_api_base: str
    litellm_proxy_api_key: str
    litellm_model: str
    # Master key — used by admin tooling against the proxy, not by the
    # agent's chat client. Loaded so it's available if/when we need it.
    litellm_master_key: str | None = None

    # Legacy direct-provider keys — kept optional in case a future code path
    # needs them, but the active agent goes through the LiteLLM proxy above.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    otel_exporter_otlp_endpoint: str | None = None

    # How many times the Kafka consumer retries a handler before DLQ.
    kafka_max_handler_attempts: int = 3

    # Per-user abuse caps. Both are enforced with in-process state —
    # this service runs as a single process, so an in-memory count IS
    # the global count (move the counters to Redis if it ever scales
    # horizontally).
    # Max simultaneously open agent WebSocket connections per user. A
    # connection over the cap is accepted then closed with WS 1008 so
    # the browser sees the code + reason instead of an opaque handshake
    # failure.
    max_ws_connections_per_user: int = 8
    # Max active+queued runs a single user may hold across ALL their
    # threads. Submissions over the cap get an `error` event on the
    # WebSocket (HTTP 429 on the turns endpoints); the socket stays open.
    max_concurrent_runs_per_user: int = 4

    # File storage backend. `local` (default) writes to `files_local_dir`;
    # `memory` is dict-backed (tests only); `s3` is interface-only today
    # (see `infrastructure/files/file_storage.py` — raises at construction
    # so a misconfig fails loud at boot).
    files_storage_backend: str = "local"
    # Where LocalFileStorage writes. Default is `/tmp/praxis-files` so
    # the service boots in any container without volume / permission
    # gymnastics — `/tmp` is universally writable. Files DO NOT survive
    # restarts at this path. For production, override via env to a
    # path you've volume-mounted (e.g. `/var/lib/praxis/files`) so
    # uploads persist across pod restarts.
    files_local_dir: str = "/tmp/praxis-files"

    # Vector store backend. `qdrant` (default) connects to the URL
    # below; `memory` keeps everything in-process (dev smoke runs and
    # any setup where Qdrant isn't reachable). Like `files_storage_backend`
    # the choice is locked at boot.
    vector_store_backend: str = "qdrant"
    # Qdrant connection. `qdrant_url` is the HTTP base URL
    # (`http://qdrant:6333` in compose). `qdrant_api_key` is required
    # for Qdrant Cloud, optional/None for self-hosted with no auth.
    # `qdrant_collection` is the single collection name used for ALL
    # users — per-user isolation is enforced via a payload filter on
    # `owner_id`, not separate collections (Qdrant performs better with
    # one large filtered collection than thousands of small ones).
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "praxis_knowledge"

    # Embedding model the LiteLLM proxy routes to. Picked to match the
    # vector dimension we provision in Qdrant (`embedding_vector_size`).
    # If you change one, change the other together — mismatched dims
    # silently corrupt search results.
    embedding_model: str = "text-embedding-3-small"
    embedding_vector_size: int = 1536  # text-embedding-3-small default

    # How many of the MOST RECENT user turns keep their attachments
    # at full fidelity. Older attachments get compacted to a stub by
    # `AttachmentCompactionMiddleware` (image bytes drop, tool-result
    # text drops, replaced with `[Attachment cleared — was: <caption>.
    # Re-fetch via read_attachment(id).]`). Lower = more aggressive
    # eviction = lower per-turn token cost on long conversations.
    # 0 = evict everything older than the current turn.
    attachment_compaction_keep_turns: int = 3

    # Text/PDF attachment pagination. The preload middleware injects
    # only the FIRST `attachment_preview_chars` of an attached file —
    # enough for the model to know what the file is and answer most
    # questions — with a footer telling it to call `read_attachment`
    # with an offset when it needs more. Each explicit tool call then
    # returns up to `attachment_page_chars` per page. Keeps a 500KB
    # CSV from dumping itself into the context on upload.
    attachment_preview_chars: int = 4_000
    attachment_page_chars: int = 20_000

    # Conversation-history compaction (CompactionMiddleware) — keeps long
    # threads under the model's context window. Levels 1-3 (collapse /
    # truncate / microcompact) are free; Level 4 replaces old history
    # with a structured LLM summary once the effective view crosses
    # `compaction_trigger_fraction` of `compaction_max_input_tokens`.
    # `compaction_max_input_tokens` is declared here (not introspected)
    # because the LiteLLM proxy hides the upstream model's profile —
    # set it to the real context window of whatever `litellm_model`
    # routes to. `compaction_keep_messages` is how many of the most
    # recent messages survive a Level 4 summarization untouched.
    compaction_max_input_tokens: int = 128_000
    compaction_trigger_fraction: float = 0.85
    compaction_keep_messages: int = 10

    # Memory service base URL. The agent's HttpMemoryClient calls this for
    # memory_search / memory_store tool calls.
    memory_service_url: str = "http://memory-service:8001"

    @property
    def kafka_broker_list(self) -> list[str]:
        return [b.strip() for b in self.kafka_brokers.split(",") if b.strip()]


def load_env() -> Env:
    return Env()  # type: ignore[call-arg]
