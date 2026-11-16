"""Integration-test harness: a real Taiga 6 in Docker, the real server as a
subprocess, and a real MCP client over streamable HTTP. No mocks."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "tests" / "stack"
COMPOSE = ["docker", "compose", "-f", str(STACK / "docker-compose.yml")]
TAIGA_PORT = int(os.environ.get("TAIGA_TEST_PORT", "18001"))
TAIGA_URL = f"http://127.0.0.1:{TAIGA_PORT}"
KEEP_STACK = os.environ.get("KEEP_STACK") == "1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def compose(*args, **kw):
    env = {**os.environ, "TAIGA_TEST_PORT": str(TAIGA_PORT)}
    return subprocess.run([*COMPOSE, *args], env=env, **kw)


@dataclass
class Seed:
    project_id: int
    slug: str
    tokens: dict[str, str]
    user_ids: dict[str, int]
    us_status_ids: dict[str, int]
    task_status_ids: dict[str, int]


@pytest.fixture(scope="session")
def taiga() -> Seed:
    compose("down", "-v", "--remove-orphans", capture_output=True)
    up = compose("up", "-d", capture_output=True, text=True)
    assert up.returncode == 0, up.stderr
    try:
        deadline = time.time() + 240
        while True:
            try:
                if httpx.get(f"{TAIGA_URL}/api/v1/", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.time() < deadline, "Taiga did not come up:\n" + \
                compose("logs", "--tail", "40", capture_output=True, text=True).stdout
            time.sleep(2)
        with open(STACK / "seed.py") as f:
            r = compose("exec", "-T", "back", "python", "manage.py", "shell",
                         stdin=f, capture_output=True, text=True)
        line = next((ln for ln in r.stdout.splitlines()
                     if ln.startswith("SEED_RESULT ")), None)
        assert line, f"seeding failed:\n{r.stdout}\n{r.stderr}"
        d = json.loads(line[len("SEED_RESULT "):])
        assert d["project_id"] == 1, "fresh DB expected (reference scripts use pk=1)"
        yield Seed(
            project_id=d["project_id"], slug=d["slug"],
            tokens={k: v["token"] for k, v in d["tokens"].items()},
            user_ids={k: v["user_id"] for k, v in d["tokens"].items()},
            us_status_ids=d["us_status_ids"], task_status_ids=d["task_status_ids"])
    finally:
        if not KEEP_STACK:
            compose("down", "-v", "--remove-orphans", capture_output=True)


@dataclass
class McpServer:
    url: str
    log_path: Path


@pytest.fixture(scope="session")
def mcp_server(taiga: Seed, tmp_path_factory):
    port = _free_port()
    log_path = tmp_path_factory.mktemp("srv") / "server.log"
    env = {**os.environ, "TAIGA_URL": TAIGA_URL,
           "TAIGA_PROJECT_ID": str(taiga.project_id)}
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "taiga_mcp.cli", "serve", "--port", str(port)],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while True:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).text == "ok":
                    break
            except httpx.HTTPError:
                pass
            assert proc.poll() is None, log_path.read_text()
            assert time.time() < deadline, "server did not start"
            time.sleep(0.2)
        yield McpServer(url=f"http://127.0.0.1:{port}/mcp", log_path=log_path)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()


@dataclass
class ToolResult:
    is_error: bool
    text: str
    data: dict | None


class Agent:
    """A real MCP client over HTTP, authenticating as one Taiga user."""

    def __init__(self, url: str, token: str | None):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    @contextlib.asynccontextmanager
    async def session(self):
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as http:
            async with streamable_http_client(self.url, http_client=http) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    yield s

    async def list_tools(self) -> list[str]:
        async with self.session() as s:
            return [t.name for t in (await s.list_tools()).tools]

    async def call(self, name: str, **args) -> ToolResult:
        args = {k: v for k, v in args.items() if v is not None}
        async with self.session() as s:
            res = await s.call_tool(name, args)
        text = "".join(getattr(c, "text", "") for c in res.content)
        data = res.structuredContent
        if data is None and not res.isError:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        return ToolResult(bool(res.isError), text, data)

    async def ok(self, name: str, **args) -> dict:
        r = await self.call(name, **args)
        assert not r.is_error, f"{name} failed: {r.text}"
        return r.data

    async def err(self, name: str, **args) -> str:
        r = await self.call(name, **args)
        assert r.is_error, f"{name} unexpectedly succeeded: {r.data}"
        return r.text


@pytest.fixture(scope="session")
def agent_a(mcp_server, taiga) -> Agent:
    return Agent(mcp_server.url, taiga.tokens["agent-a"])


@pytest.fixture(scope="session")
def agent_b(mcp_server, taiga) -> Agent:
    return Agent(mcp_server.url, taiga.tokens["agent-b"])


@pytest.fixture(scope="session")
def agent_limited(mcp_server, taiga) -> Agent:
    """A role identical to Agent but without modify_epic."""
    return Agent(mcp_server.url, taiga.tokens["agent-limited"])


@pytest.fixture(scope="session")
def admin(mcp_server, taiga) -> Agent:
    """Superuser token: used where a test needs more than the Agent role."""
    return Agent(mcp_server.url, taiga.tokens["admin"])


class Raw:
    """Direct Taiga REST access, to verify the server's effects independently."""

    def __init__(self, base: str, token: str):
        self.c = httpx.Client(base_url=base + "/api/v1", timeout=30,
                              headers={"Authorization": f"Application {token}"})

    def get(self, path, **params):
        r = self.c.get(path, params=params, headers={"x-disable-pagination": "True"})
        r.raise_for_status()
        return r.json()

    def post(self, path, body):
        r = self.c.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def patch(self, path, body):
        return self.c.patch(path, json=body)

    def delete(self, path):
        return self.c.delete(path)


@pytest.fixture(scope="session")
def raw_a(taiga) -> Raw:
    return Raw(TAIGA_URL, taiga.tokens["agent-a"])


@pytest.fixture(scope="session")
def raw_admin(taiga) -> Raw:
    return Raw(TAIGA_URL, taiga.tokens["admin"])


@pytest.fixture
def uniq() -> str:
    return "t" + uuid.uuid4().hex[:8]
