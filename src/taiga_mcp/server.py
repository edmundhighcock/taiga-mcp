"""MCP server wiring: streamable HTTP, per-request pass-through auth."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import tools
from .client import TaigaClient
from .errors import AuthError, TaigaMcpError
from .service import Session

ItemType = Literal["story", "task"]


@dataclass
class Config:
    taiga_url: str
    default_project_id: int | None = None
    host: str = "127.0.0.1"
    port: int = 8095

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        url = os.environ.get("TAIGA_URL", "").strip()
        if not url:
            raise SystemExit("TAIGA_URL is not set (e.g. http://9547a9e0-taiga:8001)")
        pid = os.environ.get("TAIGA_PROJECT_ID", "").strip()
        cfg = cls(taiga_url=url, default_project_id=int(pid) if pid else None)
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg


def bearer_token(headers) -> str | None:
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class RequireBearer:
    """ASGI middleware: no token, no access (401), except /health."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            from starlette.datastructures import Headers
            if bearer_token(Headers(scope=scope)) is None:
                resp = JSONResponse(
                    {"error": "missing_token",
                     "message": "Authorization: Bearer <your Taiga application "
                                "token> is required. This server stores no "
                                "credentials and has no anonymous access."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="taiga-mcp"'})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_server(cfg: Config, client: TaigaClient) -> FastMCP:

    local = cfg.host in ("127.0.0.1", "localhost", "::1")
    mcp = FastMCP(
        "taiga",
        instructions=(
            "Create, find, update, move and close Taiga user stories and tasks. "
            "Items are addressed by ref (the #number people see). Statuses and "
            "epics are given by name; the error messages list valid choices. "
            "Nothing can be deleted."),
        host=cfg.host, port=cfg.port,
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=local,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]),
    )

    def session(ctx: Context, project_id: int | None) -> Session:
        request = ctx.request_context.request
        token = bearer_token(request.headers) if request is not None else None
        if token is None:
            raise ToolError(str(AuthError(
                "No token: send Authorization: Bearer <your Taiga application token>.")))
        return Session(client, token, cfg.default_project_id, project_id)

    async def run(ctx: Context, project_id, fn, *args, **kw):
        try:
            return await fn(session(ctx, project_id), *args, **kw)
        except TaigaMcpError as e:
            raise ToolError(str(e)) from None

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request):
        return PlainTextResponse("ok")

    P = "project_id: optional Taiga project id; defaults to the server's TAIGA_PROJECT_ID."

    @mcp.tool()
    async def whoami(ctx: Context, project_id: int | None = None) -> dict:
        """Who this token belongs to, their roles and their permissions in the project."""
        return await run(ctx, project_id, tools.whoami)

    @mcp.tool()
    async def list_epics(ctx: Context, project_id: int | None = None) -> dict:
        """List the project's epics (ref, id, subject, status, is_closed, story count)."""
        return await run(ctx, project_id, tools.list_epics)

    @mcp.tool()
    async def list_statuses(ctx: Context, item_type: ItemType,
                            project_id: int | None = None) -> dict:
        """List the valid status names for 'story' or 'task' (with is_closed).
        Story and task statuses are separate tables."""
        return await run(ctx, project_id, tools.list_statuses, item_type)

    @mcp.tool()
    async def search_stories(ctx: Context, text: str | None = None,
                             status: str | None = None, epic: str | None = None,
                             open_only: bool = True, assigned_to_me: bool = False,
                             project_id: int | None = None) -> dict:
        """Find user stories. text matches subject/description/tags; status and
        epic are names. open_only (default true) hides closed stories unless an
        explicit status is given. assigned_to_me limits to the calling agent."""
        return await run(ctx, project_id, tools.search_stories, text, status,
                         epic, open_only, assigned_to_me)

    @mcp.tool()
    async def get_story(ctx: Context, ref: int, project_id: int | None = None) -> dict:
        """One story by ref, with description and its tasks."""
        return await run(ctx, project_id, tools.get_story, ref)

    @mcp.tool()
    async def create_story(ctx: Context, subject: str, description: str | None = None,
                           epic: str | None = None, status: str | None = None,
                           due_date: str | None = None, tags: list[str] | None = None,
                           project_id: int | None = None) -> dict:
        """Create a story; returns its ref and id. epic and status are names;
        due_date is YYYY-MM-DD; tags is a list of strings. Linking an epic
        needs the modify_epic permission."""
        return await run(ctx, project_id, tools.create_story, subject, description,
                         epic, status, due_date, tags)

    @mcp.tool()
    async def update_story(ctx: Context, ref: int, subject: str | None = None,
                           description: str | None = None, due_date: str | None = None,
                           tags: list[str] | None = None, epic: str | None = None,
                           project_id: int | None = None) -> dict:
        """Edit a story. Only the given fields change. due_date '' clears it; tags []
        clears them. epic (name) ADDS a link to that epic (never removes links) and
        needs the modify_epic permission. Use set_status to change status."""
        return await run(ctx, project_id, tools.update_story, ref, subject,
                         description, due_date, tags, epic)

    @mcp.tool()
    async def set_status(ctx: Context, item_type: ItemType, ref: int, status_name: str,
                         project_id: int | None = None) -> dict:
        """Move a story or task to a status, by name (looked up in that type's table)."""
        return await run(ctx, project_id, tools.set_status, item_type, ref, status_name)

    @mcp.tool()
    async def close(ctx: Context, item_type: ItemType, ref: int,
                    status_name: str | None = None,
                    project_id: int | None = None) -> dict:
        """Close a story (default status 'Done') or task (default 'Closed'). The
        status must be a closed one; story options include Archived, Cancelled, On Hold."""
        return await run(ctx, project_id, tools.close, item_type, ref, status_name)

    @mcp.tool()
    async def comment(ctx: Context, item_type: ItemType, ref: int, text: str,
                      project_id: int | None = None) -> dict:
        """Add a comment to a story or task (shown in Taiga under the calling agent)."""
        return await run(ctx, project_id, tools.comment, item_type, ref, text)

    @mcp.tool()
    async def list_tasks(ctx: Context, story_ref: int | None = None,
                         open_only: bool = True,
                         project_id: int | None = None) -> dict:
        """List tasks, optionally those of one story. open_only (default true) hides
        closed tasks."""
        return await run(ctx, project_id, tools.list_tasks, story_ref, open_only)

    @mcp.tool()
    async def create_task(ctx: Context, subject: str, story_ref: int | None = None,
                          description: str | None = None, status: str | None = None,
                          due_date: str | None = None, tags: list[str] | None = None,
                          project_id: int | None = None) -> dict:
        """Create a task, optionally under a story (story_ref). status is a task
        status name; due_date is YYYY-MM-DD. Returns ref and id."""
        return await run(ctx, project_id, tools.create_task, subject, story_ref,
                         description, status, due_date, tags)

    @mcp.tool()
    async def update_task(ctx: Context, ref: int, subject: str | None = None,
                          description: str | None = None, due_date: str | None = None,
                          tags: list[str] | None = None, story_ref: int | None = None,
                          project_id: int | None = None) -> dict:
        """Edit a task. due_date '' clears it; story_ref moves it to another story.
        Use set_status / close to change status."""
        return await run(ctx, project_id, tools.update_task, ref, subject,
                         description, due_date, tags, story_ref)

    return mcp


def build_app(cfg: Config):
    """The ASGI app: streamable HTTP at /mcp, /health, bearer required."""
    client = TaigaClient(cfg.taiga_url)
    app = build_server(cfg, client).streamable_http_app()
    app.add_middleware(RequireBearer)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            try:
                yield state
            finally:
                await client.aclose()

    app.router.lifespan_context = lifespan
    return app
