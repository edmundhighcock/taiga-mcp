"""Lists must not silently stop at one page (Taiga's default page is 30)."""

from concurrent.futures import ThreadPoolExecutor

import httpx

N = 45


async def test_search_and_list_return_everything(agent_a, raw_a, uniq, taiga):
    def mk(i):
        return raw_a.post("/userstories",
                          {"project": taiga.project_id, "subject": f"{uniq} bulk {i:02d}"})
    with ThreadPoolExecutor(8) as ex:
        stories = list(ex.map(mk, range(N)))
    # The trap is real: without x-disable-pagination Taiga pages at 30.
    r = raw_a.c.get("/userstories", params={"project": taiga.project_id, "q": uniq})
    assert len(r.json()) < N and r.headers.get("x-paginated") == "true"

    out = await agent_a.ok("search_stories", text=uniq)
    assert out["count"] == N
    assert len({s["ref"] for s in out["stories"]}) == N

    # tasks beyond 30, under one story
    def mkt(i):
        return raw_a.post("/tasks", {"project": taiga.project_id, "subject": f"{uniq} t{i}",
                                     "user_story": stories[1]["id"]})
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(mkt, range(N)))
    tasks = await agent_a.ok("list_tasks", story_ref=stories[1]["ref"])
    assert tasks["count"] == N
    g = await agent_a.ok("get_story", ref=stories[1]["ref"])
    assert len(g["tasks"]) == N
