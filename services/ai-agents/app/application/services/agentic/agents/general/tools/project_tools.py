"""
Project sandbox tools for the general agent.

Five tools, one responsibility each:
  - run_command_in_sandbox   : execute a shell command inside the project sandbox
  - write_file_in_sandbox    : write content to a file in the sandbox filesystem
  - read_file_in_sandbox     : read a file from the sandbox filesystem
  - list_files_in_sandbox    : list files in a sandbox directory
  - get_sandbox_stream_url   : retrieve the VNC/stream URL for the sandbox UI

The agent does NOT pass a sandbox id. Each tool resolves the bound project
from `config.configurable["project_id"]` (set by the runner) and asks the
sandbox service to get-or-create that project's sandbox — so the sandbox is
provisioned on first use and shared with the workspace UI. Operations run in
the project's persistent `/workspace`.

All five are built via factory functions that capture the `sandbox_service_url`
in a closure, keeping configuration out of the tool list itself.
"""
from __future__ import annotations

from typing import Annotated

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

_DEFAULT_SANDBOX_SERVICE_URL = "http://sandbox-service:8004"

# Shown when a sandbox tool is invoked outside a project workspace (no
# project bound to the thread) — e.g. in a normal /chat conversation.
_NO_PROJECT = (
    "[tool error] no project is bound to this conversation. Sandbox tools "
    "only work inside a project workspace."
)


def _project_id(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    v = configurable.get("project_id")
    return v if isinstance(v, str) and v else None


async def _ensure_sandbox(sandbox_service_url: str, project_id: str) -> str:
    """Get-or-create the project's sandbox and return its id. The sandbox
    service reuses a running container for the project (matched by label) or
    provisions one (mounting the project's persistent /workspace volume).
    Raises on failure; callers convert that to a tool-error string."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{sandbox_service_url}/sandbox",
            json={"project_id": project_id},
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["sandbox_id"]


def make_run_command_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `run_command_in_sandbox` tool with `sandbox_service_url` in its closure."""

    @tool
    async def run_command_in_sandbox(
        command: Annotated[str, "Shell command to execute inside the sandbox."],
        config: RunnableConfig = None,
    ) -> str:
        """Run a shell command in the project sandbox. Returns stdout, stderr, and exit code.

        Use this to execute build steps, run tests, install dependencies, invoke
        compilers or interpreters, or inspect the environment. Working directory
        is the project's persistent /workspace. Prefer this over
        read_file_in_sandbox when you need to run a program rather than view a file.

        Returns a structured string with exit_code, stdout, and stderr.
        """
        project_id = _project_id(config)
        if project_id is None:
            return _NO_PROJECT
        if not command.strip():
            return "[tool error] empty command."
        try:
            sandbox_id = await _ensure_sandbox(sandbox_service_url, project_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{sandbox_service_url}/sandbox/{sandbox_id}/exec",
                    json={"cmd": command},
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return f"[tool error] sandbox exec HTTP {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] sandbox exec failed: {exc}"
        return (
            f"exit_code={data.get('exit_code', '?')}\n"
            f"stdout={data.get('stdout', '')}\n"
            f"stderr={data.get('stderr', '')}"
        )

    return run_command_in_sandbox


def make_write_file_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `write_file_in_sandbox` tool with `sandbox_service_url` in its closure."""

    @tool
    async def write_file_in_sandbox(
        file_path: Annotated[str, "Absolute path to the file inside the sandbox (e.g. '/workspace/main.py')."],
        content: Annotated[str, "Text content to write to the file."],
        config: RunnableConfig = None,
    ) -> str:
        """Write content to a file in the project sandbox.

        Use this to create or overwrite source files, configuration files,
        scripts, or any other text-based assets inside the sandbox. Parent
        directories are created automatically. Keep project files under
        /workspace so they persist across sandbox restarts.

        Returns confirmation with the path written.
        """
        project_id = _project_id(config)
        if project_id is None:
            return _NO_PROJECT
        if not file_path.strip():
            return "[tool error] empty file_path."
        try:
            sandbox_id = await _ensure_sandbox(sandbox_service_url, project_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{sandbox_service_url}/sandbox/{sandbox_id}/files/write",
                    json={"path": file_path, "content": content},
                    timeout=60,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"[tool error] write_file HTTP {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] write_file failed: {exc}"
        return f"Written to {file_path}"

    return write_file_in_sandbox


def make_read_file_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `read_file_in_sandbox` tool with `sandbox_service_url` in its closure."""

    @tool
    async def read_file_in_sandbox(
        file_path: Annotated[str, "Absolute path to the file inside the sandbox (e.g. '/workspace/app.py')."],
        config: RunnableConfig = None,
    ) -> str:
        """Read the content of a file in the project sandbox.

        Use this to inspect source files, logs, configuration, or any
        text-based asset after writing or running commands. For binary
        files, prefer run_command_in_sandbox with `xxd` or `file`.

        Returns the raw text content of the file.
        """
        project_id = _project_id(config)
        if project_id is None:
            return _NO_PROJECT
        if not file_path.strip():
            return "[tool error] empty file_path."
        try:
            sandbox_id = await _ensure_sandbox(sandbox_service_url, project_id)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{sandbox_service_url}/sandbox/{sandbox_id}/files/read",
                    params={"path": file_path},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return f"[tool error] read_file HTTP {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] read_file failed: {exc}"
        content = data.get("content")
        if content is None:
            return "[tool error] sandbox returned no 'content' field."
        return content

    return read_file_in_sandbox


def make_list_files_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `list_files_in_sandbox` tool with `sandbox_service_url` in its closure."""

    @tool
    async def list_files_in_sandbox(
        directory: Annotated[str, "Absolute directory path to list (e.g. '/workspace')."] = "/workspace",
        config: RunnableConfig = None,
    ) -> str:
        """List files in a directory within the project sandbox.

        Use this to explore the sandbox filesystem — check which files exist,
        confirm a write landed, or discover the project layout before reading
        specific files. Returns `ls -la` output. Defaults to /workspace.
        """
        project_id = _project_id(config)
        if project_id is None:
            return _NO_PROJECT
        dir_path = directory.strip() or "/workspace"
        try:
            sandbox_id = await _ensure_sandbox(sandbox_service_url, project_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{sandbox_service_url}/sandbox/{sandbox_id}/exec",
                    json={"cmd": f"ls -la {dir_path}"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return f"[tool error] list_files HTTP {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] list_files failed: {exc}"
        stdout = data.get("stdout", "")
        stderr = data.get("stderr", "")
        if not stdout and stderr:
            return f"[tool error] ls failed: {stderr}"
        return stdout

    return list_files_in_sandbox


def make_get_sandbox_stream_url_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `get_sandbox_stream_url` tool with `sandbox_service_url` in its closure."""

    @tool
    async def get_sandbox_stream_url(
        config: RunnableConfig = None,
    ) -> str:
        """Get the VNC stream URL for the project sandbox.

        Use this when the user wants to view or share a live visual stream of
        the sandbox desktop (e.g. to watch a browser or GUI app running). Returns
        the URL the user can open in their browser.
        """
        project_id = _project_id(config)
        if project_id is None:
            return _NO_PROJECT
        try:
            sandbox_id = await _ensure_sandbox(sandbox_service_url, project_id)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{sandbox_service_url}/sandbox/{sandbox_id}/stream-url",
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return f"[tool error] get_stream_url HTTP {exc.response.status_code}: {exc.response.text}"
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] get_stream_url failed: {exc}"
        url = data.get("url")
        if not url:
            return "[tool error] sandbox returned no 'url' field."
        return url

    return get_sandbox_stream_url


def make_project_tools(
    *,
    sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL,
) -> list[BaseTool]:
    """Build and return all five project sandbox tools as a list.

    Pass `sandbox_service_url` to override the default internal Docker network
    address (useful for tests or non-Docker deployments).
    """
    return [
        make_run_command_tool(sandbox_service_url=sandbox_service_url),
        make_write_file_tool(sandbox_service_url=sandbox_service_url),
        make_read_file_tool(sandbox_service_url=sandbox_service_url),
        make_list_files_tool(sandbox_service_url=sandbox_service_url),
        make_get_sandbox_stream_url_tool(sandbox_service_url=sandbox_service_url),
    ]


# Convenience list for callers that use the default Docker-internal URL.
PROJECT_TOOLS: list[BaseTool] = make_project_tools()
