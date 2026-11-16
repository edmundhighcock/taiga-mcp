"""Agents never delete: no tool, and Taiga itself refuses the Agent role."""

import re


async def test_no_delete_like_tool(agent_a):
    for name in await agent_a.list_tools():
        assert not re.search(r"delete|remove|destroy|purge|unlink", name), name


async def test_taiga_denies_direct_delete_to_agent_role(agent_a, raw_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    t = await agent_a.ok("create_task", subject=f"{uniq} t", story_ref=s["ref"])
    assert raw_a.delete(f"/userstories/{s['id']}").status_code == 403
    assert raw_a.delete(f"/tasks/{t['id']}").status_code == 403
    epic = next(e for e in raw_a.get("/epics", project=1) if e["subject"] == "Garden")
    assert raw_a.delete(f"/epics/{epic['id']}").status_code == 403
    assert raw_a.get(f"/epics/{epic['id']}")["id"] == epic["id"]
    # still there
    assert raw_a.get(f"/userstories/{s['id']}")["subject"].endswith(" s")
    assert raw_a.get(f"/tasks/{t['id']}")["id"] == t["id"]


def test_server_code_has_no_delete_path():
    from taiga_mcp.client import TaigaClient
    assert not [m for m in dir(TaigaClient) if "delete" in m.lower()]
