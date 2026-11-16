"""Optimistic concurrency: PATCH carries the item's version."""

import asyncio

import pytest

from taiga_mcp.client import TaigaClient, is_version_conflict
from taiga_mcp.errors import VersionConflict
from taiga_mcp.service import Session
from tests.conftest import TAIGA_URL


def test_taiga_stale_version_response_shape(raw_a):
    """Pins what the real instance returns, which client.is_version_conflict
    relies on: HTTP 400, body {"version": "...doesn't match..."}."""
    s = raw_a.post("/userstories", {"project": 1, "subject": "shape probe"})
    ok = raw_a.patch(f"/userstories/{s['id']}", {"subject": "a", "version": s["version"]})
    assert ok.status_code == 200
    stale = raw_a.patch(f"/userstories/{s['id']}", {"subject": "b", "version": s["version"]})
    assert stale.status_code == 400
    body = stale.json()
    assert list(body) == ["version"]
    assert is_version_conflict(stale.status_code, body)
    # a different 400 must NOT look like a conflict
    bad = raw_a.patch(f"/userstories/{s['id']}", {"due_date": "x", "version": ok.json()["version"]})
    assert bad.status_code == 400 and not is_version_conflict(400, bad.json())
    # a missing version is also a 400, with a different body
    nov = raw_a.patch(f"/userstories/{s['id']}", {"subject": "c"})
    assert nov.status_code == 400 and not is_version_conflict(400, nov.json())


def test_taiga_conflict_is_field_aware(raw_a):
    """Production-relevant: Taiga only rejects a stale version when the same
    field was changed since; a stale PATCH touching other fields is accepted."""
    s = raw_a.post("/userstories", {"project": 1, "subject": "field aware"})
    v = s["version"]
    assert raw_a.patch(f"/userstories/{s['id']}",
                       {"description": "d", "version": v}).status_code == 200
    assert raw_a.patch(f"/userstories/{s['id']}",
                       {"tags": ["t"], "version": v}).status_code == 200
    assert raw_a.patch(f"/userstories/{s['id']}",
                       {"description": "d2", "version": v}).status_code == 400


async def test_stale_version_is_refetched_and_retried(taiga, agent_a, raw_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} orig")
    stale = raw_a.get(f"/userstories/{s['id']}")             # writer 1's view
    other = raw_a.patch(f"/userstories/{s['id']}",           # writer 2 gets in first
                        {"subject": f"{uniq} by writer 2", "description": "w2",
                         "version": stale["version"]})
    assert other.status_code == 200
    # confirm the raw stale PATCH really is a conflict (so the retry is exercised)
    assert raw_a.patch(f"/userstories/{s['id']}", {"subject": "x",
                       "version": stale["version"]}).status_code == 400

    client = TaigaClient(TAIGA_URL)
    try:
        sess = Session(client, taiga.tokens["agent-a"], taiga.project_id)
        out = await sess.patch_item("story", stale, {"subject": f"{uniq} by writer 1"})
    finally:
        await client.aclose()
    assert out["subject"] == f"{uniq} by writer 1"
    final = raw_a.get(f"/userstories/{s['id']}")
    assert final["subject"] == f"{uniq} by writer 1"      # last writer wins on the field
    assert final["description"] == "w2"                    # other fields untouched


async def test_second_conflict_gives_a_clear_error(taiga, agent_a, raw_a, uniq, monkeypatch):
    s = await agent_a.ok("create_story", subject=f"{uniq} orig")
    path = f"/userstories/{s['id']}"
    stale = raw_a.get(path)
    raw_a.patch(path, {"subject": "w2", "version": stale["version"]})

    client = TaigaClient(TAIGA_URL)
    try:
        sess = Session(client, taiga.tokens["agent-a"], taiga.project_id)
        real_get = sess.get
        calls = {"n": 0}

        async def racing_get(p, what, params=None):
            # a third writer lands between our re-read and our retry
            data = await real_get(p, what, params)
            if p == path:
                calls["n"] += 1
                raw_a.patch(path, {"subject": "w3", "version": data["version"]})
            return data

        monkeypatch.setattr(sess, "get", racing_get)
        with pytest.raises(VersionConflict) as ei:
            await sess.patch_item("story", stale, {"subject": "w1"})
    finally:
        await client.aclose()
    assert calls["n"] == 1                       # retried once, not forever
    assert "changed by someone else twice" in str(ei.value)
    assert raw_a.get(path)["subject"] == "w3"


async def test_two_agents_writing_the_same_field_concurrently(agent_a, agent_b, raw_a, uniq):
    """Neither agent gets an error; the last write wins. (Writes to DIFFERENT
    fields in truly simultaneous requests can still lose one in Taiga itself:
    each worker saves the whole row. Not something a client can fix.)"""
    for i in range(4):
        s = await agent_a.ok("create_story", subject=f"{uniq} race{i}")
        r1, r2 = await asyncio.gather(
            agent_a.call("update_story", ref=s["ref"], description="from a"),
            agent_b.call("update_story", ref=s["ref"], description="from b"))
        assert not r1.is_error and not r2.is_error, (r1.text, r2.text)
        assert raw_a.get(f"/userstories/{s['id']}")["description"] in ("from a", "from b")
