"""Taiga operations, one Session per MCP request (one agent token).

Status tables are per object type in Taiga and their ids are independent
sequences that collide (story status 1 and task status 1 are different
things). Every status lookup here goes through the table for the item type
and only ever uses ids taken from that same table.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .client import TaigaClient
from .errors import BadRequest, NotFound, PermissionDenied, VersionConflict

KINDS = {
    "story": {"endpoint": "userstories", "statuses": "userstory-statuses",
              "label": "story", "default_close": "Done"},
    "task": {"endpoint": "tasks", "statuses": "task-statuses",
             "label": "task", "default_close": "Closed"},
}


def kind_info(item_type: str) -> dict:
    key = (item_type or "").strip().lower()
    if key not in KINDS:
        raise BadRequest(
            f"Unknown item_type {item_type!r}. Valid choices: story, task.")
    return KINDS[key]


def norm_date(value: str | None) -> str | None:
    """'' clears the date; otherwise it must be YYYY-MM-DD."""
    if value is None:
        return None
    if value == "":
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise BadRequest(
            f"due_date must be YYYY-MM-DD (for example 2026-12-24), got {value!r}."
        ) from None


def norm_tags(tags: Any) -> list[str] | None:
    if tags is None:
        return None
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise BadRequest("tags must be a list of strings.")
    return [t.strip() for t in tags if t.strip()]


def story_view(d: dict) -> dict:
    return {
        "ref": d["ref"],
        "id": d["id"],
        "subject": d["subject"],
        "status": (d.get("status_extra_info") or {}).get("name"),
        "is_closed": bool(d.get("is_closed")),
        "epics": [e["subject"] for e in d.get("epics") or []],
        "due_date": d.get("due_date"),
        "tags": [t[0] if isinstance(t, list) else t for t in d.get("tags") or []],
        "assigned_to": (d.get("assigned_to_extra_info") or {}).get("full_name_display"),
    }


def task_view(d: dict) -> dict:
    us = d.get("user_story_extra_info") or {}
    return {
        "ref": d["ref"],
        "id": d["id"],
        "subject": d["subject"],
        "status": (d.get("status_extra_info") or {}).get("name"),
        "is_closed": bool((d.get("status_extra_info") or {}).get("is_closed")),
        "story_ref": us.get("ref"),
        "due_date": d.get("due_date"),
        "tags": [t[0] if isinstance(t, list) else t for t in d.get("tags") or []],
        "assigned_to": (d.get("assigned_to_extra_info") or {}).get("full_name_display"),
    }


class Session:
    def __init__(self, client: TaigaClient, token: str,
                 default_project_id: int | None, project_id: int | None = None):
        pid = project_id if project_id is not None else default_project_id
        if pid is None:
            raise BadRequest(
                "No project_id given and the server has no default "
                "(TAIGA_PROJECT_ID is not set).")
        self.client = client
        self.token = token
        self.project_id = int(pid)
        self._project: dict | None = None

    # -- low level -------------------------------------------------------
    async def get(self, path, what, params=None):
        return await self.client.get(path, self.token, what=what, params=params)

    async def get_list(self, path, what, params=None):
        return await self.client.get_list(path, self.token, what=what, params=params)

    async def project(self) -> dict:
        if self._project is None:
            self._project = await self.get(
                f"/projects/{self.project_id}",
                f"read project {self.project_id}")
        return self._project

    async def me(self) -> dict:
        return await self.get("/users/me", "read your own user")

    # -- statuses --------------------------------------------------------
    async def statuses(self, item_type: str) -> list[dict]:
        info = kind_info(item_type)
        rows = await self.get_list(
            "/" + info["statuses"], f"list {info['label']} statuses",
            {"project": self.project_id})
        return sorted(rows, key=lambda r: r.get("order", 0))

    async def resolve_status(self, item_type: str, name: str) -> dict:
        info = kind_info(item_type)
        rows = await self.statuses(item_type)
        want = (name or "").strip().casefold()
        for r in rows:
            if r["name"].casefold() == want:
                return r
        raise BadRequest(
            f"Unknown {info['label']} status {name!r}. Valid {info['label']} "
            f"statuses: {_status_choices(rows)}.")

    # -- epics -----------------------------------------------------------
    async def epics(self) -> list[dict]:
        return await self.get_list("/epics", "list epics",
                                   {"project": self.project_id})

    async def resolve_epic(self, name: str) -> dict:
        rows = await self.epics()
        want = (name or "").strip().casefold()
        for r in rows:
            if r["subject"].casefold() == want:
                return r
        if want.startswith("#") and want[1:].isdigit():
            for r in rows:
                if r["ref"] == int(want[1:]):
                    return r
        raise BadRequest(
            f"Unknown epic {name!r}. Valid epics: "
            f"{', '.join(r['subject'] for r in rows) or '(none)'}.")

    async def require_epic_link_permission(self) -> None:
        # Taiga requires modify_epic to link a story to an epic.
        perms = (await self.project()).get("my_permissions") or []
        if "modify_epic" not in perms:
            raise PermissionDenied(
                "Permission denied: linking a story to an epic needs the "
                "'modify_epic' permission, which this agent's Taiga role does "
                "not have. Nothing was changed; retry without the epic, or ask "
                "the household admin to grant it.")

    async def link_epic(self, epic: dict, story_id: int) -> None:
        # Taiga needs BOTH epic and user_story in the body.
        await self.client.post(
            f"/epics/{epic['id']}/related_userstories", self.token,
            {"epic": epic["id"], "user_story": story_id},
            what=f"link the story to epic {epic['subject']!r}")

    # -- items -----------------------------------------------------------
    async def get_item(self, item_type: str, ref: int) -> dict:
        info = kind_info(item_type)
        try:
            return await self.get(
                f"/{info['endpoint']}/by_ref",
                f"read {info['label']} #{ref}",
                {"ref": ref, "project": self.project_id})
        except NotFound:
            raise NotFound(
                f"No {info['label']} with ref #{ref} in project "
                f"{self.project_id}.") from None

    async def patch_item(self, item_type: str, item: dict, fields: dict,
                         what: str | None = None) -> dict:
        """PATCH with the item's version; on a conflict re-GET and retry once."""
        info = kind_info(item_type)
        what = what or f"update {info['label']} #{item['ref']}"
        path = f"/{info['endpoint']}/{item['id']}"
        try:
            return await self.client.patch(
                path, self.token, {**fields, "version": item["version"]}, what=what)
        except VersionConflict:
            fresh = await self.get(path, f"re-read {info['label']} #{item['ref']}")
        try:
            return await self.client.patch(
                path, self.token, {**fields, "version": fresh["version"]}, what=what)
        except VersionConflict:
            raise VersionConflict(
                f"{info['label'].capitalize()} #{item['ref']} was changed by "
                f"someone else twice in a row while updating it. Nothing was "
                f"lost; read it again and retry.") from None

    async def tasks_for_story(self, story_id: int) -> list[dict]:
        return await self.get_list(
            "/tasks", "list tasks",
            {"project": self.project_id, "user_story": story_id})


def _status_choices(rows: list[dict]) -> str:
    opn = [r["name"] for r in rows if not r.get("is_closed")]
    closed = [r["name"] for r in rows if r.get("is_closed")]
    s = f"open: {', '.join(opn) or '-'}"
    if closed:
        s += f"; closed: {', '.join(closed)}"
    return s
