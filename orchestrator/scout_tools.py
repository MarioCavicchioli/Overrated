"""Custom tool routing for the Scout's session, for person 1's Scout loop.

Builders run inside the Scout's workflow runs, so their custom tool calls arrive
on the Scout's session stream. Route every custom tool call through this:

    router = ScoutToolRouter(store, data)
    router.register("save_candidates", my_scout_handler)   # the Scout's own tools
    run_session(client, store, ..., agent_name="scout", tool_handler=router)

or, in a hand-written loop:

    text = router(event.name, event.input)

Builder tool names are reserved; registering one of them raises.
"""

from __future__ import annotations

from typing import Any

from agents.builder.handlers import TOOLS as BUILDER_TOOLS
from agents.builder.handlers import BuilderHandlers
from orchestrator.data_client import DataClient
from orchestrator.sessions import ToolHandler
from orchestrator.store import Store


class ScoutToolRouter:
    def __init__(self, store: Store, data: DataClient):
        self.builder = BuilderHandlers(store, data)
        self.extra: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        if name in BUILDER_TOOLS:
            raise ValueError(f"{name} is a Case builder tool")
        self.extra[name] = handler

    def __call__(self, name: str, args: dict[str, Any]) -> str:
        if self.builder.handles(name):
            return self.builder.handle(name, args)
        if name in self.extra:
            return self.extra[name](name, args)
        return f"ERROR: unknown tool {name}"
