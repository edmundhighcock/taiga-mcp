"""The tool implementations (transport-independent, take a Session)."""

from __future__ import annotations

from .errors import BadRequest
from .service import (Session, kind_info, norm_date, norm_tags, story_view,
                      task_view)


def _view(item_type: str, d: dict) -> dict:
    return story_view(d) if kind_info(item_type)["label"] == "story" else task_view(d)


async def whoami(s: Session) -> dict:
    me = await s.me()
    proj = await s.project()
    return {
        "id": me["id"],
        "username": me["username"],
        "full_name": me.get("full_name"),
        "roles": me.get("roles") or [],
        "project": {"id": proj["id"], "name": proj["name"], "slug": proj["slug"]},
        "project_permissions": sorted(proj.get("my_permissions") or []),
    }


async def list_epics(s: Session) -> dict:
    rows = await s.epics()
    return {"epics": [
        {"ref": e["ref"], "id": e["id"], "subject": e["subject"],
         "status": (e.get("status_extra_info") or {}).get("name"),
         "is_closed": bool(e.get("is_closed")),
         "stories": (e.get("user_stories_counts") or {}).get("total")}
        for e in sorted(rows, key=lambda e: e["ref"])]}


async def list_statuses(s: Session, item_type: str) -> dict:
    rows = await s.statuses(item_type)
    return {"item_type": kind_info(item_type)["label"],
            "statuses": [{"name": r["name"], "is_closed": bool(r.get("is_closed"))}
                         for r in rows]}


async def search_stories(s: Session, text=None, status=None, epic=None,
                         open_only=True, assigned_to_me=False) -> dict:
    params: dict = {"project": s.project_id}
    if text:
        params["q"] = text
    if status:
        # An explicit status wins over open_only.
        params["status"] = (await s.resolve_status("story", status))["id"]
    elif open_only:
        params["status__is_closed"] = "false"
    if epic:
        params["epic"] = (await s.resolve_epic(epic))["id"]
    if assigned_to_me:
        params["assigned_to"] = (await s.me())["id"]
    rows = await s.get_list("/userstories", "search stories", params)
    stories = [story_view(r) for r in sorted(rows, key=lambda r: r["ref"])]
    return {"count": len(stories), "stories": stories}


async def get_story(s: Session, ref: int) -> dict:
    d = await s.get_item("story", ref)
    out = story_view(d)
    out["description"] = d.get("description") or ""
    out["tasks"] = [task_view(t) for t in
                    sorted(await s.tasks_for_story(d["id"]), key=lambda t: t["ref"])]
    return out


async def create_story(s: Session, subject, description=None, epic=None,
                       status=None, due_date=None, tags=None) -> dict:
    if not subject or not subject.strip():
        raise BadRequest("subject must not be empty.")
    body: dict = {"project": s.project_id, "subject": subject.strip()}
    if description is not None:
        body["description"] = description
    if status:
        body["status"] = (await s.resolve_status("story", status))["id"]
    if due_date:
        body["due_date"] = norm_date(due_date)
    if tags is not None:
        body["tags"] = norm_tags(tags)
    epic_row = None
    if epic:
        epic_row = await s.resolve_epic(epic)
        await s.require_epic_link_permission()   # before creating anything
    d = await s.client.post("/userstories", s.token, body, what="create a story")
    if epic_row:
        await s.link_epic(epic_row, d["id"])
        d = await s.get_item("story", d["ref"])
    return story_view(d)


async def update_story(s: Session, ref, subject=None, description=None,
                       due_date=None, tags=None, epic=None) -> dict:
    d = await s.get_item("story", ref)
    fields: dict = {}
    if subject is not None:
        if not subject.strip():
            raise BadRequest("subject must not be empty.")
        fields["subject"] = subject.strip()
    if description is not None:
        fields["description"] = description
    if due_date is not None:
        fields["due_date"] = norm_date(due_date) or None
    if tags is not None:
        fields["tags"] = norm_tags(tags)
    epic_row = None
    if epic:
        epic_row = await s.resolve_epic(epic)
        await s.require_epic_link_permission()
    if not fields and not epic_row:
        raise BadRequest("Nothing to update: give at least one of subject, "
                         "description, due_date, tags, epic.")
    if fields:
        d = await s.patch_item("story", d, fields)
    if epic_row and epic_row["id"] not in [e["id"] for e in d.get("epics") or []]:
        await s.link_epic(epic_row, d["id"])   # adds a link; never unlinks
    return story_view(await s.get_item("story", ref))


async def set_status(s: Session, item_type, ref, status_name) -> dict:
    st = await s.resolve_status(item_type, status_name)
    d = await s.get_item(item_type, ref)
    d = await s.patch_item(item_type, d, {"status": st["id"]})
    return _view(item_type, d)


async def close(s: Session, item_type, ref, status_name=None) -> dict:
    info = kind_info(item_type)
    status_name = status_name or info["default_close"]
    st = await s.resolve_status(item_type, status_name)
    if not st.get("is_closed"):
        rows = await s.statuses(item_type)
        closed = ", ".join(r["name"] for r in rows if r.get("is_closed"))
        raise BadRequest(
            f"{st['name']!r} is not a closed {info['label']} status, so it "
            f"cannot be used to close. Closed {info['label']} statuses: {closed}.")
    return await set_status(s, item_type, ref, st["name"])


async def comment(s: Session, item_type, ref, text) -> dict:
    if not text or not text.strip():
        raise BadRequest("comment text must not be empty.")
    d = await s.get_item(item_type, ref)
    d = await s.patch_item(item_type, d, {"comment": text},
                           what=f"comment on {kind_info(item_type)['label']} #{ref}")
    out = _view(item_type, d)
    out["total_comments"] = d.get("total_comments")
    return out


async def list_tasks(s: Session, story_ref=None, open_only=True) -> dict:
    params: dict = {"project": s.project_id}
    if story_ref is not None:
        params["user_story"] = (await s.get_item("story", story_ref))["id"]
    if open_only:
        params["status__is_closed"] = "false"
    rows = await s.get_list("/tasks", "list tasks", params)
    tasks = [task_view(r) for r in sorted(rows, key=lambda r: r["ref"])]
    return {"count": len(tasks), "tasks": tasks}


async def create_task(s: Session, subject, story_ref=None, description=None,
                      status=None, due_date=None, tags=None) -> dict:
    if not subject or not subject.strip():
        raise BadRequest("subject must not be empty.")
    body: dict = {"project": s.project_id, "subject": subject.strip()}
    if story_ref is not None:
        body["user_story"] = (await s.get_item("story", story_ref))["id"]
    if description is not None:
        body["description"] = description
    if status:
        body["status"] = (await s.resolve_status("task", status))["id"]
    if due_date:
        body["due_date"] = norm_date(due_date)
    if tags is not None:
        body["tags"] = norm_tags(tags)
    d = await s.client.post("/tasks", s.token, body, what="create a task")
    return task_view(await s.get_item("task", d["ref"]))


async def update_task(s: Session, ref, subject=None, description=None,
                      due_date=None, tags=None, story_ref=None) -> dict:
    d = await s.get_item("task", ref)
    fields: dict = {}
    if subject is not None:
        if not subject.strip():
            raise BadRequest("subject must not be empty.")
        fields["subject"] = subject.strip()
    if description is not None:
        fields["description"] = description
    if due_date is not None:
        fields["due_date"] = norm_date(due_date) or None
    if tags is not None:
        fields["tags"] = norm_tags(tags)
    if story_ref is not None:
        fields["user_story"] = (await s.get_item("story", story_ref))["id"]
    if not fields:
        raise BadRequest("Nothing to update: give at least one of subject, "
                         "description, due_date, tags, story_ref.")
    d = await s.patch_item("task", d, fields)
    return task_view(await s.get_item("task", ref))
