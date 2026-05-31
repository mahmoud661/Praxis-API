"""
Liveness + readiness probes. Liveness is a static "process is up"; readiness
pings the shared psycopg pool owned by `AgenticStore` (the same pool the
LangGraph Store + Checkpointer use).
"""

from __future__ import annotations

from ...infrastructure.agentic.agentic_store import AgenticStore


class HealthController:
    def __init__(self, agentic_store: AgenticStore) -> None:
        self._agentic = agentic_store

    async def liveness(self) -> dict[str, str]:
        return {"status": "ok"}

    async def readiness(self) -> dict[str, object]:
        checks: dict[str, str] = {}
        try:
            async with self._agentic.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            checks["db"] = "ok"
        except Exception as err:  # noqa: BLE001
            checks["db"] = str(err)
        ready = all(v == "ok" for v in checks.values())
        return {"ready": ready, "checks": checks}
