"""Pure workspace domain logic — no IO, no HTTP, no provider awareness.

Everything here operates on strings the application layer obtained from a
sandbox (via the ISandboxClient port) and returns plain data structures.
Keeping these as pure functions makes them trivially unit-testable and keeps
both the route layer (HTTP only) and the adapters (IO only) free of policy.
"""
from __future__ import annotations

import shlex

# ── Project bootstrap ─────────────────────────────────────────────────────────

# Runs the user's declared setup commands (`praxis setup` reads
# /workspace/.praxis) on sandbox boot. Guarded: no config file → no-op, and
# a failure never propagates (the sandbox must still come up).
PROJECT_SETUP_CMD = (
    "[ -f /workspace/.praxis ] && "
    "praxis setup >>/tmp/praxis-setup.log 2>&1 || true"
)

# ── Listening-port detection ──────────────────────────────────────────────────

# Reads the kernel's TCP socket tables; needs no tooling inside the image.
PROCNET_TCP_CMD = "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"

# x11vnc — infrastructure of the desktop stream, never a user app.
_INTERNAL_PORTS = frozenset({5900})


def parse_listening_ports(procnet: str) -> list[int]:
    """Parse `/proc/net/tcp{,6}` content and return TCP ports in LISTEN state
    bound to all interfaces (0.0.0.0 / ::) — those a reverse-proxy can reach.
    Loopback-only binds are skipped (unreachable from outside the sandbox)."""
    ports: set[int] = set()
    for line in procnet.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local, state = parts[1], parts[3]
        if state != "0A" or ":" not in local:  # 0A = TCP_LISTEN
            continue
        ip_hex, _, port_hex = local.rpartition(":")
        if set(ip_hex) != {"0"}:  # only all-interfaces binds are reachable
            continue
        try:
            ports.add(int(port_hex, 16))
        except ValueError:
            continue
    return sorted(ports - _INTERNAL_PORTS)


# ── Workspace file tree ───────────────────────────────────────────────────────

# Directories excluded from the file tree — huge / noise. They still show up
# as folder nodes; we just don't descend into them.
TREE_PRUNE = (
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".cache", ".mypy_cache", ".pytest_cache",
)
TREE_MAXDEPTH = 8
TREE_MAX_ENTRIES = 4000


def tree_find_cmd(path: str) -> str:
    """A single `find` that lists the workspace as `<type>\\t<relpath>` lines,
    pruning heavy dirs and bounding depth + count. GNU find only (`-printf`)."""
    q = shlex.quote(path)
    names = " -o ".join(f"-name {shlex.quote(n)}" for n in TREE_PRUNE)
    return (
        f"find {q} -mindepth 1 -maxdepth {TREE_MAXDEPTH} "
        f"\\( {names} \\) -prune -printf '%y\\t%P\\n' "
        f"-o -printf '%y\\t%P\\n' 2>/dev/null | head -n {TREE_MAX_ENTRIES}"
    )


def build_file_tree(stdout: str) -> list[dict]:
    """Turn the flat `<type>\\t<relpath>` listing into a nested tree
    (folders-first, alphabetical). Shape matches the frontend Magic UI
    `TreeViewElement` (id/name/type/children)."""
    entries: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        if "\t" not in line:
            continue
        typ, rel = line.split("\t", 1)
        if rel:
            entries.append((typ, rel))
    # Shallower paths first so a parent always exists before its children.
    entries.sort(key=lambda e: e[1].count("/"))

    nodes: dict[str, dict] = {}
    roots: list[dict] = []
    for typ, rel in entries:
        parts = rel.split("/")
        is_dir = typ == "d"
        node: dict = {
            "id": rel,
            "name": parts[-1],
            "type": "folder" if is_dir else "file",
            "children": [] if is_dir else None,
        }
        nodes[rel] = node
        parent = "/".join(parts[:-1])
        bucket = roots
        if parent:
            p = nodes.get(parent)
            if p is not None and p.get("children") is not None:
                bucket = p["children"]
        bucket.append(node)

    def _sort(level: list[dict]) -> None:
        level.sort(key=lambda n: (n["type"] != "folder", n["name"].lower()))
        for n in level:
            if n.get("children"):
                _sort(n["children"])

    _sort(roots)
    return roots
