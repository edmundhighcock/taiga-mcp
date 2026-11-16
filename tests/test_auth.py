"""Pass-through auth: no token, no access; tokens never logged."""

import httpx
import pytest

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}
HDRS = {"Accept": "application/json, text/event-stream"}


def test_no_token_is_401(mcp_server):
    r = httpx.post(mcp_server.url, json=INIT, headers=HDRS)
    assert r.status_code == 401
    assert "Bearer" in r.headers["www-authenticate"]
    assert r.json()["error"] == "missing_token"


def test_non_bearer_scheme_is_401(mcp_server):
    r = httpx.post(mcp_server.url, json=INIT,
                   headers={**HDRS, "Authorization": "Basic Zm9vOmJhcg=="})
    assert r.status_code == 401
    r = httpx.post(mcp_server.url, json=INIT,
                   headers={**HDRS, "Authorization": "Bearer"})
    assert r.status_code == 401


def test_health_needs_no_token(mcp_server):
    assert httpx.get(mcp_server.url.replace("/mcp", "/health")).text == "ok"


async def test_client_without_token_cannot_connect(mcp_server):
    from tests.conftest import Agent
    with pytest.raises(BaseException) as ei:
        await Agent(mcp_server.url, None).call("whoami")
    assert "401" in repr(ei.value) or "Unauthorized" in repr(ei.value) \
        or "ExceptionGroup" in repr(ei.value)


async def test_garbage_token_is_rejected_by_taiga(mcp_server):
    from tests.conftest import Agent
    msg = await Agent(mcp_server.url, "not-a-real-token").err("whoami")
    assert "rejected this agent's token" in msg
    msg = await Agent(mcp_server.url, "not-a-real-token").err(
        "create_story", subject="should not exist")
    assert "rejected this agent's token" in msg


async def test_tokens_never_in_server_log(mcp_server, taiga, agent_a):
    await agent_a.ok("whoami")
    await Agent_bad(mcp_server).call("whoami")
    log = mcp_server.log_path.read_text()
    for tok in taiga.tokens.values():
        assert tok not in log
        assert tok.split(":")[0] not in log
    assert "not-a-real-token" not in log


def Agent_bad(mcp_server):
    from tests.conftest import Agent
    return Agent(mcp_server.url, "not-a-real-token")
