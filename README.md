# taiga-mcp

An MCP server (streamable HTTP) that lets household AI agents create, find,
update, move and close Taiga 6 user stories and tasks. One long-running server;
each agent authenticates with its own Taiga token. There are no delete tools.

## Auth model: pass-through

- The agent sends its **own Taiga application token** as
  `Authorization: Bearer <token>` to this server.
- For each request the server calls Taiga with `Authorization: Application <token>`.
- The server **stores no credentials** and never logs tokens or request headers.
- A request without a bearer token gets HTTP 401 (`missing_token`); there is no
  anonymous access. `GET /health` is the only open path.
- A token Taiga rejects gives a tool error ("Taiga rejected this agent's token").
- Taiga enforces permissions per agent, and its history shows the acting agent.
  A missing permission is reported plainly ("this agent's Taiga role does not allow ...").

## Environment

| Variable | Meaning |
| --- | --- |
| `TAIGA_URL` | Required. Taiga back-end base URL, API under `/api/v1/` (production: `http://9547a9e0-taiga:8001`). |
| `TAIGA_PROJECT_ID` | Default project id (production: `1`, "Life"). Every tool also takes an optional `project_id`. |

    TAIGA_URL=http://localhost:8001 TAIGA_PROJECT_ID=1 \
      taiga-mcp serve --host 0.0.0.0 --port 8095      # MCP at http://host:8095/mcp

The `Dockerfile` runs exactly that (`ENTRYPOINT taiga-mcp`, `CMD serve --host 0.0.0.0 --port 8095`).

## Tools

Items are addressed by **ref** (the `#258` people see). Statuses and epics are
given by **name**, case-insensitively; errors list the valid choices. Results are
concise structured objects: `ref`, `id`, `subject`, `status`, `is_closed`,
`epics` (names), `due_date`, `tags`, `assigned_to`; tasks carry `story_ref`.

| Tool | Notes |
| --- | --- |
| `whoami` | username, roles, project, and the token's permissions in the project |
| `list_epics` | ref, id, subject, status, is_closed, story count |
| `list_statuses(item_type)` | `story` or `task`, with `is_closed` |
| `search_stories(text?, status?, epic?, open_only=true, assigned_to_me?)` | an explicit `status` overrides `open_only` |
| `get_story(ref)` | adds description and the story's tasks |
| `create_story(subject, description?, epic?, status?, due_date?, tags?)` | returns ref and id |
| `update_story(ref, subject?, description?, due_date?, tags?, epic?)` | `due_date=""` / `tags=[]` clear; `epic` adds a link, never unlinks |
| `set_status(item_type, ref, status_name)` | looked up in that type's own status table |
| `close(item_type, ref, status_name?)` | default `Done` (story) / `Closed` (task); must be a closed status |
| `comment(item_type, ref, text)` | |
| `list_tasks(story_ref?, open_only=true)` | |
| `create_task(subject, story_ref?, description?, status?, due_date?, tags?)` | |
| `update_task(ref, subject?, description?, due_date?, tags?, story_ref?)` | `story_ref` moves the task |

Dates are `YYYY-MM-DD` (epics have none). Tags are lists of strings.

Behaviour worth knowing:

- Story and task status ids are independent sequences that collide (id 1 is both
  "Todo" and "New" on Life). Names are always resolved in the table for the item type.
- Writes are `PATCH` with the item's `version`. Taiga answers a stale version with
  HTTP 400 `{"version": "The version doesn't match with the current one"}`; the
  server re-reads and retries once. Taiga only raises this when the *same field*
  changed since; edits to different fields do not conflict.
- All lists send `x-disable-pagination: True` (Taiga otherwise pages at 30).
- **Epic links need Taiga's `modify_epic` permission, which the Agent role does not
  have.** `create_story`/`update_story` with `epic` fail up front with a clear message
  (nothing is created) for such tokens. `search_stories(epic=...)` and `list_epics` work.
- The server only issues GET, POST and PATCH to Taiga.

## Claude Code client

Keep the token in a file readable only by you (`chmod 600 ~/.config/taiga/agent.token`)
and have a launcher export it, or use `headersHelper`. `.mcp.json`:

```json
{
  "mcpServers": {
    "taiga": {
      "type": "http",
      "url": "http://taiga-mcp.lan:8095/mcp",
      "headersHelper": "sh -c 'printf \"{\\\"Authorization\\\": \\\"Bearer %s\\\"}\" \"$(cat ~/.config/taiga/agent.token)\"'"
    }
  }
}
```

If your Claude Code version lacks `headersHelper`, use environment expansion and
set the variable from the file when launching:
`"headers": {"Authorization": "Bearer ${TAIGA_TOKEN}"}` with
`TAIGA_TOKEN=$(cat ~/.config/taiga/agent.token) claude`.

Agent users and tokens are created by the scripts in the `ham` repo,
`services/taiga-agent-access/` (`create_agent_role.py`, `create_agent_user.py`).

## Tests

    make test

Needs Docker and `uv`. It starts `postgres:12-alpine` and the official
`taigaio/taiga-back:6.0.9` (no rabbitmq/celery; events use the Postgres backend),
seeds a "Life" project through the Django shell (`tests/stack/seed.py`: production's
custom story and task statuses, two epics, the Agent role, two agent users with
unusable passwords and application tokens, plus a superuser), runs the server as a
subprocess and drives it with a real MCP client over HTTP, then removes containers
and volumes. Taiga listens on `127.0.0.1:18001` (override with `TAIGA_TEST_PORT`);
the MCP server uses a free port. About 90 seconds, most of it Taiga's migrations.
`make test-keep` leaves the stack up; `make stack-down` removes it. `make test
ARGS="-k conflict"` selects tests.

The tests cover every tool, no-token and bad-token requests, the absence of delete
(and Taiga's 403 for a direct DELETE by the Agent role), version conflicts, status
resolution across the colliding tables, epic links, pagination beyond 30 items,
per-agent attribution in Taiga's history, and the production role/user scripts run
verbatim.

## Dependencies

Exact pins in `pyproject.toml`, locked in `uv.lock`: `mcp==1.30.0` (the 1.x line;
2.x renamed FastMCP), `httpx`, `uvicorn` and `starlette` (every import is a direct
dependency). Python 3.12.
