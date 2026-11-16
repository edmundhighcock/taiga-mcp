"""The production provisioning scripts in the ham repo, run verbatim against
the real Taiga 6 (they are written for 6.0.10-p9; the stack runs 6.0.9)."""

from pathlib import Path

import httpx
import pytest

from tests.conftest import TAIGA_URL, compose

REF = Path("/home/edmundhighcock/Code/ham/services/taiga-agent-access")
pytestmark = pytest.mark.skipif(not REF.exists(), reason="ham repo not present")


def _run(script, *env):
    args = ["exec", "-T"]
    for e in env:
        args += ["-e", e]
    with open(REF / script) as f:
        return compose(*args, "back", "python", "manage.py", "shell",
                       stdin=f, capture_output=True, text=True)


async def test_role_and_user_scripts_work_and_token_authenticates(mcp_server, taiga):
    from tests.conftest import Agent
    r = _run("create_agent_role.py")
    assert "17 permissions" in r.stdout, r.stdout + r.stderr
    env = ("AGENT_NAME=agent-ref", "TOKEN_FILE=/tmp/agent-ref.token")
    for _ in range(2):   # second run: idempotent
        r = _run("create_agent_user.py", *env)
        assert "token written" in r.stdout, r.stdout + r.stderr
    tok = compose("exec", "-T", "back", "cat", "/tmp/agent-ref.token",
                  capture_output=True, text=True).stdout.strip()
    me = await Agent(mcp_server.url, tok).ok("whoami")
    assert me["username"] == "agent-ref" and me["roles"] == ["Agent"]
    assert "modify_epic" not in me["project_permissions"]
