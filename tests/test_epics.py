"""Epic links. Taiga needs modify_epic for them, which the Agent role lacks."""

import pytest


async def test_agent_role_cannot_link_epics_and_nothing_is_created(agent_a, raw_a, uniq):
    msg = await agent_a.err("create_story", subject=f"{uniq} epicless", epic="Kitchen")
    assert "modify_epic" in msg and "Permission denied" in msg
    # checked up front: no half-created story is left behind
    assert [s for s in raw_a.get("/userstories", project=1, q=uniq)] == []


async def test_taiga_itself_403s_agent_epic_link(raw_a, raw_admin, agent_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    epics = raw_a.get("/epics", project=1)
    e = next(x for x in epics if x["subject"] == "Kitchen")
    r = raw_a.c.post(f"/epics/{e['id']}/related_userstories",
                     json={"epic": e["id"], "user_story": s["id"]})
    assert r.status_code == 403
    # and Taiga insists on BOTH fields
    r = raw_admin.c.post(f"/epics/{e['id']}/related_userstories",
                         json={"user_story": s["id"]})
    assert r.status_code == 400 and "epic" in r.json()


async def test_link_via_create_and_update_with_modify_epic(admin, raw_admin, uniq):
    # the superuser token stands in for a role that has modify_epic
    s = await admin.ok("create_story", subject=f"{uniq} in kitchen", epic="Kitchen")
    assert s["epics"] == ["Kitchen"]
    other = await admin.ok("create_story", subject=f"{uniq} floating")
    assert other["epics"] == []
    moved = await admin.ok("update_story", ref=other["ref"], epic="garden")
    assert moved["epics"] == ["Garden"]
    # idempotent, and adds rather than replaces
    again = await admin.ok("update_story", ref=other["ref"], epic="Garden")
    assert again["epics"] == ["Garden"]
    both = await admin.ok("update_story", ref=other["ref"], epic="Kitchen")
    assert sorted(both["epics"]) == ["Garden", "Kitchen"]
    assert both["id"] == other["id"]
    # search by epic name
    found = await admin.ok("search_stories", epic="Kitchen", text=uniq)
    assert {x["ref"] for x in found["stories"]} == {s["ref"], other["ref"]}
    found = await admin.ok("search_stories", epic="Garden", text=uniq)
    assert [x["ref"] for x in found["stories"]] == [other["ref"]]
    # Taiga's own view of the link
    d = raw_admin.get(f"/userstories/{s['id']}")
    assert [e["subject"] for e in d["epics"]] == ["Kitchen"]


async def test_agent_can_search_by_epic(admin, agent_a, uniq):
    s = await admin.ok("create_story", subject=f"{uniq} k", epic="Kitchen")
    found = await agent_a.ok("search_stories", epic="Kitchen", text=uniq)
    assert [x["ref"] for x in found["stories"]] == [s["ref"]]
    assert found["stories"][0]["epics"] == ["Kitchen"]
