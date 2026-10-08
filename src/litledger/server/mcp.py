"""The MCP server: tools (in toolsets a client picks), resources (status, project overview, maps) and prompts (agent
briefs), served over streamable HTTP at /mcp by server/app.py."""
from __future__ import annotations

import logging
import re

import anyio
import mcp_types as types
from mcp.server.lowlevel.server import Server

from .. import __version__, maps, tools
from ..ledger import Ledger
from ..prompts import BRIEFS
from ..search import project_overview
from .rest import actor_from

log = logging.getLogger("litledger")


def _text(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def build_mcp(lg: Ledger) -> Server:
    def toolsets(ctx) -> list[str]:
        header = ctx.request.headers.get("x-litledger-tools") if ctx.request is not None else None
        return tools.toolset_names([s for s in (header or "").split(",") if s.strip()] or lg.settings.tools)

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=t.description, input_schema=tools.mcp_schema(t),
                       annotations=types.ToolAnnotations(read_only_hint=not t.write, destructive_hint=t.destructive,
                                                         open_world_hint=t.open_world))
            for t in (tools.TOOLS[name] for name in toolsets(ctx))])

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        actor = actor_from(ctx.request)
        shown = toolsets(ctx)
        if params.name not in shown:  # hidden toolsets are not callable either
            return _text(tools.unknown_tool(params.name, shown), True)
        args = params.arguments or {}
        try:  # tools.call checks `project` (and answers in text); anything else it raises is caught here
            text = await anyio.to_thread.run_sync(tools.call, lg, actor, params.name, args)
            return _text(text, text.startswith(("error:", "no_match:")))
        except Exception as exc:  # never leak a traceback into the agent's context
            log.exception("tool %s failed", params.name)
            return _text(f"error: {type(exc).__name__}: {str(exc)[:300]}", True)

    async def list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[
            types.Resource(name="status", uri="litledger://status", description="Server, sources, setup warnings", mime_type="text/plain"),
            types.Resource(name="project", uri="litledger://project", description="This project's overview and tag vocabulary",
                           mime_type="text/plain")])

    async def list_templates(ctx, params) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(resource_templates=[
            types.ResourceTemplate(name="map", uri_template="litledger://map/{id}", description="A mind map as an outline",
                                   mime_type="text/plain")])

    async def read_resource(ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        actor = actor_from(ctx.request)
        uri = str(params.uri)

        def render() -> str:
            if uri == "litledger://status":
                return tools.status_text(lg, actor)
            if uri == "litledger://project":
                return project_overview(lg, actor.project)
            m = re.match(r"^litledger://map/(.+)$", uri)
            if m:
                return maps.outline(maps.get_map(lg, actor.project, m.group(1)))
            raise ValueError(f"unknown resource {uri}")

        text = await anyio.to_thread.run_sync(render)
        return types.ReadResourceResult(contents=[types.TextResourceContents(uri=uri, mime_type="text/plain", text=text)])

    async def list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[
            types.Prompt(name=name, description=desc, arguments=[types.PromptArgument(name=arg, required=arg == args[0]) for arg in args])
            for name, (_, desc, args) in BRIEFS.items()])

    async def get_prompt(ctx, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        if params.name not in BRIEFS:
            raise ValueError(f"unknown prompt {params.name}")
        fn, desc, _ = BRIEFS[params.name]
        text = fn(**{k: v for k, v in (params.arguments or {}).items() if v})
        return types.GetPromptResult(description=desc, messages=[
            types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))])

    return Server("litledger", version=__version__, instructions=tools.instructions(lg), on_list_tools=list_tools,
                  on_call_tool=call_tool, on_list_resources=list_resources, on_list_resource_templates=list_templates,
                  on_read_resource=read_resource, on_list_prompts=list_prompts, on_get_prompt=get_prompt)
