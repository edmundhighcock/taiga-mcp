"""Taiga's history must show which agent did what (pass-through auth)."""


def _who(raw, kind, item_id):
    return [(h["user"]["username"], h["comment"]) for h in raw.get(f"/history/{kind}/{item_id}")]


async def test_history_names_the_acting_agent(agent_a, agent_b, raw_admin, taiga, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} shared")
    await agent_a.ok("comment", item_type="story", ref=s["ref"], text="note from a")
    await agent_b.ok("comment", item_type="story", ref=s["ref"], text="note from b")
    await agent_b.ok("set_status", item_type="story", ref=s["ref"], status_name="In progress")
    hist = raw_admin.get(f"/history/userstory/{s['id']}")
    by_comment = {h["comment"]: h["user"]["username"] for h in hist if h["comment"]}
    assert by_comment == {"note from a": "agent-a", "note from b": "agent-b"}
    # the status change (no comment) is b's as well
    changes = [h for h in hist if "status" in (h.get("values_diff") or {})]
    assert [h["user"]["username"] for h in changes] == ["agent-b"]
    d = raw_admin.get(f"/userstories/{s['id']}")
    assert d["owner"] == taiga.user_ids["agent-a"]


async def test_task_attribution(agent_a, agent_b, raw_admin, taiga, uniq):
    t = await agent_b.ok("create_task", subject=f"{uniq} task")
    await agent_a.ok("update_task", ref=t["ref"], subject=f"{uniq} renamed by a")
    d = raw_admin.get(f"/tasks/{t['id']}")
    assert d["owner"] == taiga.user_ids["agent-b"]
    users = [h["user"]["username"] for h in raw_admin.get(f"/history/task/{t['id']}")]
    assert users == ["agent-a"]


async def test_whoami_differs_per_token(agent_a, agent_b):
    assert (await agent_a.ok("whoami"))["username"] == "agent-a"
    assert (await agent_b.ok("whoami"))["username"] == "agent-b"
