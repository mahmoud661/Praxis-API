"""
Project sandbox tools for the general agent.

Five tools, one responsibility each:
  - run_command_in_sandbox   : execute a shell command inside the project sandbox
  - write_file_in_sandbox    : write content to a file in the sandbox filesystem
  - read_file_in_sandbox     : read a file from the sandbox filesystem
  - list_files_in_sandbox    : list files in a sandbox directory
  - get_sandbox_stream_url   : retrieve the VNC/stream URL for the sandbox UI

`sandbox_id` is passed explicitly by the agent — unlike memory tools which
scope to `owner_id` from RunnableConfig, sandbox operations require a
caller-supplied sandbox identifier because one user may have many projects.

All five are built via factory functions that capture the `sandbox_service_url`
in a closure, keeping configuration out of the tool list itself.
"""
from __future__ import annotations

from typing import Annotated

import httpx
from langchain_core.tools import BaseTool, tool

_DEFAULT_SANDBOX_SERVICE_URL = "http://sandbox:8004"


def make_run_command_tool(*, sandbox_service_url: str = _DEFAULT_SANDBOX_SERVICE_URL) -> BaseTool:
    """Return the `run_command_in_sandbox` tool with `sandbox_service_url` in its closure."""

    @tool
    async def run_command_in_sandbox(
        sandbox_id: Annotated[str, "The ID of the sandbox to run the command in."],
        command: Annotated[str, "Shell command to execute inside the sandbox."],
    ) -> str:
        """Run a shell command in the project sandbox. Returns stdout, stderr, and exit code.

        Use this to execute build steps, run tests, install dependencies, invoke
        compilers or interpreters, or inspect the environment. Prefer this over
        read_file_in_sandbox when you need to run a program rather than view a file.

        Returns a structured string with exit_code, stdout, and stderr.
        """
        if not sandbox_id.strip():
            return "[tool error] empty sandbox_id."
        if not command.strip():
            return "[tool error] empty command."
        try:
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
        sandbox_id: Annotated[str, "The ID of the sandbox to write the file in."],
        file_path: Annotated[str, "Absolute path to the file inside the sandbox (e.g. '/home/user/main.py')."],
        content: Annotated[str, "Text content to write to the file."],
    ) -> str:
        """Write content to a file in the project sandbox.

        Use this to create or overwrite source files, configuration files,
        scripts, or any other text-based assets inside the sandbox. The
        parent directory must already exist; use run_command_in_sandbox with
        `mkdir -p` first if needed.

        Returns confirmation with the path written.
        """
        if not sandbox_id.strip():
            return "[tool error] empty sandbox_id."
        if not file_path.strip():
            return "[tool error] empty file_path."
        try:
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
        sandbox_id: Annotated[str, "The ID of the sandbox to read the file from."],
        file_path: Annotated[str, "Absolute path to the file inside the sandbox (e.g. '/home/user/app.py')."],
    ) -> str:
        """Read the content of a file in the project sandbox.

        Use this to inspect source files, logs, configuration, or any
        text-based asset after writing or running commands. For binary
        files, prefer run_command_in_sandbox with `xxd` or `file`.

        Returns the raw text content of the file.
        """
        if not sandbox_id.strip():
            return "[tool error] empty sandbox_id."
        if not file_path.strip():
            return "[tool error] empty file_path."
        try:
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
        sandbox_id: Annotated[str, "The ID of the sandbox to list files in."],
        directory: Annotated[str, "Absolute directory path to list (e.g. '/home/user')."] = "/home/user",
    ) -> str:
        """List files in a directory within the project sandbox.

        Use this to explore the sandbox filesystem — check which files exist,
        confirm a write landed, or discover the project layout before reading
        specific files. Returns `ls -la` output.
        """
        if not sandbox_id.strip():
            return "[tool error] empty sandbox_id."
        dir_path = directory.strip() or "/home/user"
        try:
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
        sandbox_id: Annotated[str, "The ID of the sandbox to get the stream URL for."],
    ) -> str:
        """Get the VNC stream URL for the project sandbox.

        Use this when the user wants to view or share a live visual stream of
        the sandbox desktop (e.g. to watch a browser or GUI app running). Returns
        the URL the user can open in their browser.
        """
        if not sandbox_id.strip():
            return "[tool error] empty sandbox_id."
        try:
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
