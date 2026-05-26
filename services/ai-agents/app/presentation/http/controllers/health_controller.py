from __future__ import annotations

from fastapi import APIRouter

from ....infrastructure.persistence.postgres_connection import PostgresConnection


def make_router(*, conn: PostgresConnection) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/healthz")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz")
    async def readiness() -> dict[str, object]:
        checks: dict[str, str] = {}
        try:
            async with conn.pool.acquire() as c:
                await c.fetchval("SELECT 1")
            checks["db"] = "ok"
        except Exception as err:  # noqa: BLE001
            checks["db"] = str(err)
        ready = all(v == "ok" for v in checks.values())
        return {"ready": ready, "checks": checks}

    return router
