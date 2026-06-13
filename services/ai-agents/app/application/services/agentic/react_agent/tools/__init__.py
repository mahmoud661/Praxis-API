"""Library tools — generic, host-agnostic tools that ship with the
react_agent base. They depend only on the ports in `react_agent.ports`
(the host injects implementations at graph-build time).

Agent-SPECIFIC tools (search over a particular knowledge base, calls
into a host service) do NOT belong here — they live in the host's
per-agent `tools/` folder."""

from .read_attachment import make_read_attachment_tool, materialize_attachment

__all__ = ["make_read_attachment_tool", "materialize_attachment"]
