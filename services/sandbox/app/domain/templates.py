"""Sandbox project templates — pure domain data, no IO.

A template describes how to turn an EMPTY workspace into a runnable starter
project:

  files         written verbatim by the application layer (deterministic,
                no network needed)
  scaffold_cmd  optional shell command for generator-based stacks (Vite);
                staged in /tmp then copied in, so interactive "directory not
                empty" prompts can never hang the boot
  praxis        the `.praxis` config written to the workspace root — the same
                contract `praxis init` produces, so auto-setup on restart and
                the Run button work identically for templated projects.

Applying a template to a non-empty workspace is the application layer's
responsibility to prevent (existing project volumes must never be clobbered).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SandboxTemplate:
    id: str
    name: str
    description: str
    praxis: dict
    files: dict[str, str] = field(default_factory=dict)
    scaffold_cmd: str | None = None


def _vite_scaffold(vite_template: str) -> str:
    """Scaffold a Vite starter into /workspace via a /tmp staging dir.

    The generated config is patched with `server: { host: true }` so the dev
    server binds 0.0.0.0 even when the user runs a plain `npm run dev` —
    loopback-only binds are invisible to port detection and unreachable by
    the preview proxy."""
    return (
        f"cd /tmp && rm -rf scaffold && "
        f"npm create vite@latest scaffold -- --template {vite_template} && "
        f"cp -a /tmp/scaffold/. /workspace/ && rm -rf /tmp/scaffold && "
        "sed -i 's/defineConfig({/defineConfig({\\n  server: { host: true },/' "
        "/workspace/vite.config.* || true"
    )


_EXPRESS_SERVER = """\
const express = require("express");

const app = express();
const port = process.env.PORT || 3000;

app.get("/", (_req, res) => {
  res.send("Hello from your sandbox!");
});

app.listen(port, "0.0.0.0", () => {
  console.log(`listening on http://0.0.0.0:${port}`);
});
"""

_EXPRESS_PACKAGE_JSON = """\
{
  "name": "app",
  "version": "1.0.0",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "express": "^4.19.0"
  }
}
"""

_FASTAPI_MAIN = """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Hello from your sandbox!"}
"""

TEMPLATES: dict[str, SandboxTemplate] = {
    t.id: t
    for t in (
        SandboxTemplate(
            id="react",
            name="React",
            description="Vite + React frontend, dev server on port 5173.",
            scaffold_cmd=_vite_scaffold("react"),
            praxis={
                "setup": ["npm install"],
                "start": "npm run dev -- --host 0.0.0.0",
                "ports": [5173],
            },
        ),
        SandboxTemplate(
            id="vue",
            name="Vue",
            description="Vite + Vue 3 frontend, dev server on port 5173.",
            scaffold_cmd=_vite_scaffold("vue"),
            praxis={
                "setup": ["npm install"],
                "start": "npm run dev -- --host 0.0.0.0",
                "ports": [5173],
            },
        ),
        SandboxTemplate(
            id="express",
            name="Node + Express",
            description="Minimal Express API server on port 3000.",
            files={
                "/workspace/package.json": _EXPRESS_PACKAGE_JSON,
                "/workspace/server.js": _EXPRESS_SERVER,
            },
            praxis={
                "setup": ["npm install"],
                "start": "node server.js",
                "ports": [3000],
            },
        ),
        SandboxTemplate(
            id="fastapi",
            name="Python + FastAPI",
            description="Minimal FastAPI service on port 8000.",
            files={
                "/workspace/requirements.txt": "fastapi\nuvicorn[standard]\n",
                "/workspace/main.py": _FASTAPI_MAIN,
            },
            praxis={
                "setup": ["pip install -r requirements.txt"],
                "start": "uvicorn main:app --host 0.0.0.0 --port 8000 --reload",
                "ports": [8000],
            },
        ),
    )
}


def get_template(template_id: str) -> SandboxTemplate | None:
    return TEMPLATES.get(template_id)
