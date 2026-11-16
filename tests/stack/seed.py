# Seed a fresh Taiga 6 with the "Life" project shape used in production.
# Run inside the container, script on stdin:
#   python manage.py shell < seed.py
# Prints one line "SEED_RESULT {json}" (test-only throwaway tokens).
# The role/user logic mirrors ham/services/taiga-agent-access/create_agent_*.py.
import json

from django.db import transaction
from taiga.projects.epics.models import Epic
from taiga.external_apps.models import Application, ApplicationToken
from taiga.permissions.choices import MEMBERS_PERMISSIONS
from taiga.projects.models import (Membership, Project, ProjectTemplate,
                                   TaskStatus, UserStoryStatus)
from taiga.users.models import Role, User

AGENT_PERMISSIONS = [
    "view_project",
    "view_us", "add_us", "modify_us", "comment_us",
    "view_tasks", "add_task", "modify_task", "comment_task",
    "view_issues", "add_issue", "modify_issue", "comment_issue",
    "view_epics", "view_milestones", "view_wiki_pages", "view_wiki_links",
]
_valid = {p for p, _l in MEMBERS_PERMISSIONS}
assert set(AGENT_PERMISSIONS) <= _valid

OPEN_US = ["Todo", "Time-sensitive", "In progress", "Awaiting confirmation"]
CLOSED_US = ["Done", "Archived", "Cancelled", "On Hold"]

out = {}
with transaction.atomic():
    su = User(username="admin", email="admin@invalid", full_name="Admin",
              is_active=True, is_superuser=True, is_staff=True)
    su.set_password("test-only-admin-password")
    su.save()

    tpl = ProjectTemplate.objects.get(slug="scrum")
    project = Project(name="Life", description="Household project (test copy)",
                      owner=su, is_private=True, creation_template=tpl)
    project.save()
    project.refresh_from_db()

    # Story statuses: rename the template ones in place (so ids 1..5 collide
    # with task status ids 1..5, as in production), then add the rest.
    names = OPEN_US + CLOSED_US
    existing = list(UserStoryStatus.objects.filter(project=project).order_by("order", "id"))
    n = len(existing)
    for i, (st, nm) in enumerate(zip(existing, names[:n])):
        st.name = nm
        st.slug = nm.lower().replace(" ", "-")
        st.order = i + 1
        st.is_closed = nm in CLOSED_US
        st.save()
    for i, nm in enumerate(names[n:], start=n + 1):
        UserStoryStatus.objects.create(
            project=project, name=nm, slug=nm.lower().replace(" ", "-"),
            order=i, is_closed=nm in CLOSED_US, color="#999999")
    project.default_us_status = UserStoryStatus.objects.get(project=project, name="Todo")

    # Task statuses: template gives New, In progress, Ready for test, Closed,
    # Needs Info. Only Closed is closed.
    for ts in TaskStatus.objects.filter(project=project):
        ts.is_closed = ts.name == "Closed"
        ts.save()
    assert {t.name for t in TaskStatus.objects.filter(project=project)} == \
        {"New", "In progress", "Ready for test", "Needs Info", "Closed"}
    project.default_task_status = TaskStatus.objects.get(project=project, name="New")
    project.save()

    agent_role, _ = Role.objects.get_or_create(
        project=project, slug="agent",
        defaults={"name": "Agent", "computable": False, "permissions": AGENT_PERMISSIONS,
                  "order": (Role.objects.filter(project=project).count() + 1) * 10})
    assert sorted(agent_role.permissions) == sorted(AGENT_PERMISSIONS)

    for subj in ("Kitchen", "Garden"):
        Epic.objects.create(project=project, subject=subj, owner=su)

    tokens = {}
    for name in ("agent-a", "agent-b", "admin"):
        if name == "admin":
            user = su
        else:
            user = User(username=name, email="%s@invalid" % name,
                        full_name="%s (agent)" % name.capitalize(), is_active=True)
            user.set_unusable_password()
            user.save()
            Membership.objects.create(user=user, project=project, role=agent_role,
                                      is_admin=False, email=user.email)
        app = Application.objects.create(
            name=name, next_url="http://127.0.0.1/%s/taiga-token" % name)
        tok = ApplicationToken.objects.create(application=app, user=user)
        tok.generate_token()   # signing.dumps({"app_token_id": pk}); needs pk
        tok.save()
        tokens[name] = {"token": tok.token, "user_id": user.id}

    out = {"project_id": project.id, "slug": project.slug, "tokens": tokens,
           "us_status_ids": {s.name: s.id for s in UserStoryStatus.objects.filter(project=project)},
           "task_status_ids": {s.name: s.id for s in TaskStatus.objects.filter(project=project)}}
print("SEED_RESULT " + json.dumps(out))
