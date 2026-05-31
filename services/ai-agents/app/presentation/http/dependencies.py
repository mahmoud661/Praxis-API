from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, WebSocket
from starlette.status import WS_1008_POLICY_VIOLATION


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    email: str | None
    roles: tuple[str, ...]

    def has_role(self, role: str) -> bool:
        return role in self.roles


def current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_user_roles: str | None = Header(default=None, alias="X-User-Roles"),
) -> CurrentUser:
    """The gateway verifies the session and forwards these headers. This
    service trusts them because it isn't reachable from outside the docker
    network."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"error": "UNAUTHENTICATED"})
    roles = tuple(r.strip() for r in (x_user_roles or "").split(",") if r.strip())
    return CurrentUser(id=x_user_id, email=x_user_email, roles=roles or ("user",))


def require_role(*allowed: str):
    """Dependency factory: 403 unless the caller has at least one of `allowed`."""

    def guard(user: CurrentUser = None) -> CurrentUser:  # type: ignore[assignment]
        # FastAPI resolves the nested Depends(current_user) for us when used
        # via Depends(require_role("admin")) in a route signature — see
        # examples in the controller. For direct call paths we accept the
        # already-resolved CurrentUser and re-check here.
        if user is None or not any(user.has_role(r) for r in allowed):
            raise HTTPException(
                status_code=403,
                detail={"error": "FORBIDDEN", "required": list(allowed)},
            )
        return user

    return guard


# Back-compat for callers that only need the id string.
async def current_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"error": "UNAUTHENTICATED"})
    return x_user_id


async def ws_authenticate(ws: WebSocket) -> str | None:
    """WebSocket equivalent of `current_user_id`. On a connection upgrade,
    Starlette reads HTTP headers — the gateway forwards `X-User-Id` for WS
    just like it does for HTTP. If absent, close the socket with the
    policy-violation code and return None. The caller MUST early-return on
    None and skip `ws.accept()`."""
    user_id = ws.headers.get("x-user-id")
    if not user_id:
        # Per spec, close on a not-yet-accepted socket sends an HTTP 403.
        await ws.close(code=WS_1008_POLICY_VIOLATION)
        return None
    return user_id
