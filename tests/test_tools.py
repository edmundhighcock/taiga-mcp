"""Every tool, end to end: MCP client -> server -> real Taiga."""

import pytest

EXPECTED_TOOLS = {
    "whoami", "list_epics", "list_statuses", "search_stories", "get_story",
    "create_story", "update_story", "set_status", "close", "comment",
    "list_tasks", "create_task", "update_task",
}


async def test_tool_list(agent_a):
    assert set(await agent_a.list_tools()) == EXPECTED_TOOLS


async def test_whoami(agent_a, taiga):
    me = await agent_a.ok("whoami")
    assert me["username"] == "agent-a"
    assert me["id"] == taiga.user_ids["agent-a"]
    assert me["roles"] == ["Agent"]
    assert me["project"]["name"] == "Life"
    perms = set(me["project_permissions"])
    assert "add_us" in perms and "modify_epic" not in perms
    assert not any(p.startswith("delete_") for p in perms)


async def test_list_epics(agent_a):
    epics = (await agent_a.ok("list_epics"))["epics"]
    assert {"Kitchen", "Garden"} <= {e["subject"] for e in epics}


async def test_list_statuses(agent_a):
    st = await agent_a.ok("list_statuses", item_type="story")
    assert [(s["name"], s["is_closed"]) for s in st["statuses"]] == [
        ("Todo", False), ("Time-sensitive", False), ("In progress", False),
        ("Awaiting confirmation", False), ("Done", True), ("Archived", True),
        ("Cancelled", True), ("On Hold", True)]
    tk = await agent_a.ok("list_statuses", item_type="task")
    assert {s["name"]: s["is_closed"] for s in tk["statuses"]} == {
        "New": False, "In progress": False, "Ready for test": False,
        "Needs Info": False, "Closed": True}
    msg = await agent_a.err("list_statuses", item_type="issue")
    assert "story" in msg and "task" in msg


async def test_story_lifecycle(agent_a, raw_a, uniq):
    s = await agent_a.ok(
        "create_story", subject=f"{uniq} buy bulbs", description="LED, warm",
        status="Time-sensitive", due_date="2026-12-24", tags=["home", "shop"])
    assert s["status"] == "Time-sensitive" and not s["is_closed"]
    assert s["due_date"] == "2026-12-24"
    assert sorted(s["tags"]) == ["home", "shop"]
    assert isinstance(s["ref"], int) and isinstance(s["id"], int)

    g = await agent_a.ok("get_story", ref=s["ref"])
    assert g["subject"] == f"{uniq} buy bulbs"
    assert g["description"] == "LED, warm"
    assert g["tasks"] == []

    u = await agent_a.ok("update_story", ref=s["ref"], subject=f"{uniq} buy LED bulbs",
                         description="LED, warm, E27", due_date="2027-01-02",
                         tags=["home"])
    assert u["subject"].endswith("LED bulbs") and u["due_date"] == "2027-01-02"
    assert u["tags"] == ["home"]
    # raw check of what Taiga holds
    d = raw_a.get(f"/userstories/{s['id']}")
    assert d["description"] == "LED, warm, E27" and d["due_date"] == "2027-01-02"

    cleared = await agent_a.ok("update_story", ref=s["ref"], due_date="", tags=[])
    assert cleared["due_date"] is None and cleared["tags"] == []

    found = await agent_a.ok("search_stories", text=uniq)
    assert [x["ref"] for x in found["stories"]] == [s["ref"]]
    assert found["count"] == 1

    me = await agent_a.ok("search_stories", text=uniq, assigned_to_me=True)
    assert me["count"] == 0   # nobody is assigned

    ip = await agent_a.ok("set_status", item_type="story", ref=s["ref"],
                          status_name="in progress")   # case-insensitive
    assert ip["status"] == "In progress"
    by_status = await agent_a.ok("search_stories", text=uniq, status="In progress")
    assert by_status["count"] == 1
    none = await agent_a.ok("search_stories", text=uniq, status="Todo")
    assert none["count"] == 0

    done = await agent_a.ok("close", item_type="story", ref=s["ref"])
    assert done["status"] == "Done" and done["is_closed"]
    assert (await agent_a.ok("search_stories", text=uniq))["count"] == 0   # open_only
    assert (await agent_a.ok("search_stories", text=uniq, open_only=False))["count"] == 1
    assert (await agent_a.ok("search_stories", text=uniq, status="Done"))["count"] == 1


async def test_close_with_other_closed_status(agent_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} maybe")
    c = await agent_a.ok("close", item_type="story", ref=s["ref"], status_name="Cancelled")
    assert c["status"] == "Cancelled" and c["is_closed"]


async def test_close_rejects_open_status(agent_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} x")
    msg = await agent_a.err("close", item_type="story", ref=s["ref"], status_name="Todo")
    assert "not a closed story status" in msg
    for name in ("Done", "Archived", "Cancelled", "On Hold"):
        assert name in msg


async def test_comment(agent_a, raw_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} c")
    r = await agent_a.ok("comment", item_type="story", ref=s["ref"], text="first note")
    assert r["total_comments"] == 1
    hist = raw_a.get(f"/history/userstory/{s['id']}")
    assert any(h["comment"] == "first note" for h in hist)
    t = await agent_a.ok("create_task", subject=f"{uniq} t")
    r = await agent_a.ok("comment", item_type="task", ref=t["ref"], text="task note")
    assert r["total_comments"] == 1
    assert any(h["comment"] == "task note"
               for h in raw_a.get(f"/history/task/{t['id']}"))


async def test_tasks(agent_a, raw_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} parent")
    t1 = await agent_a.ok("create_task", subject=f"{uniq} t1", story_ref=s["ref"],
                          description="d", status="Needs Info", due_date="2026-12-01")
    assert t1["status"] == "Needs Info" and t1["story_ref"] == s["ref"]
    assert t1["due_date"] == "2026-12-01"
    t2 = await agent_a.ok("create_task", subject=f"{uniq} loose")
    assert t2["story_ref"] is None and t2["status"] == "New"

    lst = await agent_a.ok("list_tasks", story_ref=s["ref"])
    assert [t["ref"] for t in lst["tasks"]] == [t1["ref"]]
    g = await agent_a.ok("get_story", ref=s["ref"])
    assert [t["ref"] for t in g["tasks"]] == [t1["ref"]]

    u = await agent_a.ok("update_task", ref=t2["ref"], subject=f"{uniq} moved",
                         story_ref=s["ref"], due_date="2027-03-03", tags=["x"])
    assert u["story_ref"] == s["ref"] and u["due_date"] == "2027-03-03"
    assert (await agent_a.ok("update_task", ref=t2["ref"], due_date=""))["due_date"] is None
    assert {t["ref"] for t in (await agent_a.ok("list_tasks", story_ref=s["ref"]))["tasks"]} \
        == {t1["ref"], t2["ref"]}

    c = await agent_a.ok("close", item_type="task", ref=t1["ref"])   # default Closed
    assert c["status"] == "Closed" and c["is_closed"]
    open_ = await agent_a.ok("list_tasks", story_ref=s["ref"])
    assert [t["ref"] for t in open_["tasks"]] == [t2["ref"]]
    allt = await agent_a.ok("list_tasks", story_ref=s["ref"], open_only=False)
    assert allt["count"] == 2
    assert raw_a.get(f"/tasks/{t1['id']}")["status_extra_info"]["is_closed"] is True


async def test_validation_errors_name_choices(agent_a, uniq):
    msg = await agent_a.err("create_story", subject=f"{uniq} z", status="Bogus")
    assert "Unknown story status 'Bogus'" in msg and "Awaiting confirmation" in msg
    msg = await agent_a.err("create_task", subject=f"{uniq} z", status="Bogus")
    assert "Unknown task status" in msg and "Ready for test" in msg
    msg = await agent_a.err("search_stories", epic="Nope")
    assert "Unknown epic" in msg and "Kitchen" in msg and "Garden" in msg
    msg = await agent_a.err("create_story", subject="x", due_date="next friday")
    assert "YYYY-MM-DD" in msg
    msg = await agent_a.err("get_story", ref=999999)
    assert "No story with ref #999999" in msg
    msg = await agent_a.err("get_story", ref=1, project_id=99999)
    assert msg   # unknown project: some clear error, not a crash
    assert "Nothing to update" in await agent_a.err("update_story", ref=1)
    assert "Nothing to update" in await agent_a.err("update_task", ref=1)


async def test_project_id_override(agent_a, taiga, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} p", project_id=taiga.project_id)
    assert s["ref"]
