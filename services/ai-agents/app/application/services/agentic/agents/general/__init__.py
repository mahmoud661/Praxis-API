"""The general agent — one self-contained package per agent:

    general/
      agent.py         the BaseAgent subclass (DI seam + spec)
      graph.py         assembly: connects everything via imports
      sections.py      state-machine sections (qualify → execute)
      prompts/         system + per-section prompts
      tools/           agent-specific tools (kb_search)
      middlewares/     agent-specific middlewares (none yet)

The registry discovers agents by importing each package's `agent`
module directly — deliberately NOT re-exported here, so importing a
light submodule (e.g. `tools.kb_search` in a unit test) doesn't drag
in `agent.py`'s runtime DI annotations (AgenticStore → postgres
checkpointer) on environments that don't have them installed.
Reusable runtime machinery comes from the react_agent library."""
