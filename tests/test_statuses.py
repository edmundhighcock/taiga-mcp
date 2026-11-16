"""Status tables are per item type and their ids collide: resolve by type."""

from collections import defaultdict


def test_precondition_ids_collide(taiga):
    """The seeded stack must reproduce production's collision, or the tests
    below prove nothing."""
    shared = set(taiga.us_status_ids.values()) & set(taiga.task_status_ids.values())
    assert shared, "no id appears in both status tables"
    by_id_us = {v: k for k, v in taiga.us_status_ids.items()}
    by_id_task = {v: k for k, v in taiga.task_status_ids.items()}
    assert any(by_id_us[i] != by_id_task[i] for i in shared)
    assert taiga.us_status_ids["In progress"] != taiga.task_status_ids["In progress"]


async def test_same_name_resolves_per_table(agent_a, raw_a, taiga, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    t = await agent_a.ok("create_task", subject=f"{uniq} t")
    await agent_a.ok("set_status", item_type="story", ref=s["ref"], status_name="In progress")
    await agent_a.ok("set_status", item_type="task", ref=t["ref"], status_name="In progress")
    sd = raw_a.get(f"/userstories/{s['id']}")
    td = raw_a.get(f"/tasks/{t['id']}")
    assert sd["status"] == taiga.us_status_ids["In progress"]
    assert td["status"] == taiga.task_status_ids["In progress"]
    assert sd["status"] != td["status"]
    assert sd["status_extra_info"]["name"] == td["status_extra_info"]["name"] == "In progress"


async def test_every_status_name_lands_on_the_right_row(agent_a, raw_a, taiga, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    t = await agent_a.ok("create_task", subject=f"{uniq} t")
    for name, sid in taiga.us_status_ids.items():
        out = await agent_a.ok("set_status", item_type="story", ref=s["ref"], status_name=name)
        assert out["status"] == name
        assert raw_a.get(f"/userstories/{s['id']}")["status"] == sid
    for name, tid in taiga.task_status_ids.items():
        out = await agent_a.ok("set_status", item_type="task", ref=t["ref"], status_name=name)
        assert out["status"] == name
        assert raw_a.get(f"/tasks/{t['id']}")["status"] == tid


async def test_names_from_the_other_table_are_rejected(agent_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    t = await agent_a.ok("create_task", subject=f"{uniq} t")
    # "New" and "Closed" are task statuses only; "Todo" and "Done" story only.
    msg = await agent_a.err("set_status", item_type="story", ref=s["ref"], status_name="New")
    assert "Unknown story status" in msg and "Todo" in msg
    msg = await agent_a.err("set_status", item_type="story", ref=s["ref"], status_name="Closed")
    assert "Unknown story status" in msg
    msg = await agent_a.err("set_status", item_type="task", ref=t["ref"], status_name="Todo")
    assert "Unknown task status" in msg and "Needs Info" in msg
    msg = await agent_a.err("close", item_type="task", ref=t["ref"], status_name="Done")
    assert "Unknown task status" in msg


async def test_close_defaults_and_is_closed_flags(agent_a, uniq):
    s = await agent_a.ok("create_story", subject=f"{uniq} s")
    t = await agent_a.ok("create_task", subject=f"{uniq} t")
    cs = await agent_a.ok("close", item_type="story", ref=s["ref"])
    ct = await agent_a.ok("close", item_type="task", ref=t["ref"])
    assert (cs["status"], cs["is_closed"]) == ("Done", True)
    assert (ct["status"], ct["is_closed"]) == ("Closed", True)
    for name in ("Archived", "Cancelled", "On Hold"):   # all closed on Life
        s2 = await agent_a.ok("create_story", subject=f"{uniq} {name}")
        c = await agent_a.ok("close", item_type="story", ref=s2["ref"], status_name=name)
        assert c["is_closed"] is True and c["status"] == name
    # reopening: moving to an open status clears is_closed
    r = await agent_a.ok("set_status", item_type="story", ref=s["ref"], status_name="Todo")
    assert r["is_closed"] is False
