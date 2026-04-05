import json
import os
import time

from django.db import transaction
from django.utils import timezone

from .models import Activity, DiscordUser, MessageLog, Project, ProjectMember, Task
from . import ollama_client


def get_admin_ids() -> list[str]:
    raw = os.environ.get('DISCORD_ADMIN_IDS', '')
    return [id_.strip() for id_ in raw.split(',') if id_.strip()]


def is_admin(discord_id: str) -> bool:
    return discord_id in get_admin_ids()


def get_or_create_discord_user(discord_id: str, username: str) -> DiscordUser:
    user, _ = DiscordUser.objects.update_or_create(
        discord_id=discord_id,
        defaults={'username': username},
    )
    return user


@transaction.atomic
def authorize_user(actor_discord_id: str, target_discord_id: str, target_username: str) -> tuple[DiscordUser, bool]:
    if not is_admin(actor_discord_id):
        raise PermissionError("Only admins can authorize users.")

    user, created = DiscordUser.objects.get_or_create(
        discord_id=target_discord_id,
        defaults={'username': target_username},
    )
    if not created:
        user.username = target_username

    user.is_authorized = True
    user.authorized_at = timezone.now()
    user.save()

    misc_project, proj_created = Project.objects.get_or_create(
        name='Miscellaneous',
        creator=user,
    )
    ProjectMember.objects.get_or_create(
        project=misc_project,
        user=user,
        defaults={'role': ProjectMember.OWNER},
    )

    return user, created


@transaction.atomic
def create_project(actor: DiscordUser, name: str, description: str = '') -> Project:
    if actor.created_projects.filter(name=name).exists():
        raise ValueError(f"You already have a project named '{name}'.")

    project = Project.objects.create(name=name, description=description, creator=actor)
    ProjectMember.objects.create(project=project, user=actor, role=ProjectMember.OWNER)
    Activity.objects.create(user=actor, action='create_project', payload={'project_id': project.id, 'name': name})
    return project


@transaction.atomic
def link_channel_to_project(actor: DiscordUser, project_name: str, channel_id: str) -> Project:
    project = Project.objects.get(name=project_name, creator=actor)

    try:
        membership = ProjectMember.objects.get(project=project, user=actor)
    except ProjectMember.DoesNotExist:
        raise PermissionError("You are not a member of this project.")

    if membership.role != ProjectMember.OWNER:
        raise PermissionError("Only project owners can link a channel.")

    if Project.objects.filter(discord_channel_id=channel_id).exclude(pk=project.pk).exists():
        raise ValueError("This channel is already linked to another project.")

    project.discord_channel_id = channel_id
    project.save()
    return project


@transaction.atomic
def add_collaborator(actor: DiscordUser, project_name: str, target: DiscordUser) -> ProjectMember:
    project = Project.objects.get(name=project_name, creator=actor)

    if not ProjectMember.objects.filter(project=project, user=actor).exists():
        raise PermissionError("You are not a member of this project.")

    if ProjectMember.objects.filter(project=project, user=target).exists():
        raise ValueError(f"{target.username} is already a member of this project.")

    member = ProjectMember.objects.create(project=project, user=target, role=ProjectMember.COLLABORATOR)
    return member


def get_user_projects(user: DiscordUser) -> list[Project]:
    project_ids = ProjectMember.objects.filter(user=user).values_list('project_id', flat=True)
    return list(Project.objects.filter(id__in=project_ids))


def get_miscellaneous_project(user: DiscordUser) -> Project:
    return Project.objects.get(name='Miscellaneous', creator=user)


@transaction.atomic
def add_task(actor: DiscordUser, project: Project, title: str, description: str = '',
             priority: int = 3, due_date=None, deadline_type: str = 'soft') -> Task:
    if not ProjectMember.objects.filter(project=project, user=actor).exists():
        raise PermissionError("You are not a member of this project.")

    task = Task.objects.create(
        project=project,
        title=title,
        description=description,
        priority=priority,
        due_date=due_date,
        deadline_type=deadline_type,
    )
    Activity.objects.create(user=actor, action='add_task', payload={'task_id': task.id, 'title': title})
    return task


@transaction.atomic
def update_task(actor: DiscordUser, task_id: int, **fields) -> Task:
    task = Task.objects.get(id=task_id)

    if not ProjectMember.objects.filter(project=task.project, user=actor).exists():
        raise PermissionError("You are not a member of this task's project.")

    allowed = {'title', 'description', 'status', 'priority', 'due_date', 'deadline_type'}
    for key, value in fields.items():
        if key in allowed:
            setattr(task, key, value)
    task.save()

    Activity.objects.create(user=actor, action='update_task', payload={'task_id': task_id, 'fields': list(fields.keys())})
    return task


def list_tasks(actor: DiscordUser, project: Project = None) -> list[Task]:
    project_ids = ProjectMember.objects.filter(user=actor).values_list('project_id', flat=True)
    qs = Task.objects.filter(project_id__in=project_ids)
    if project is not None:
        qs = qs.filter(project=project)
    return list(qs)


def build_context_summary(user: DiscordUser) -> str:
    projects = get_user_projects(user)
    lines = [f"User: {user.username}"]
    for project in projects:
        lines.append(f"\nProject: {project.name}")
        open_tasks = Task.objects.filter(project=project, status__in=[Task.TODO, Task.DOING])
        if open_tasks.exists():
            for task in open_tasks:
                lines.append(f"  - [{task.status}] (id={task.id}) {task.title} (priority={task.priority})")
        else:
            lines.append("  (no open tasks)")
    return '\n'.join(lines)


def mcp_get_projects(user: DiscordUser) -> str:
    """MCP tool: return the user's project list as a JSON string."""
    projects = get_user_projects(user)
    payload = [{"id": p.id, "name": p.name, "description": p.description or ""} for p in projects]
    return json.dumps(payload)


def mcp_get_tasks(user: DiscordUser, project_name: str) -> str:
    """
    MCP tool: return open tasks for a named project as a JSON string.

    Uses case-insensitive contains match so partial names resolve correctly.
    When multiple projects match, selects the one with the shortest name
    (i.e. the most specific match). Returns an error JSON object if no
    project is found.
    """
    project_ids = ProjectMember.objects.filter(user=user).values_list('project_id', flat=True)
    projects = Project.objects.filter(id__in=project_ids, name__icontains=project_name)
    if not projects.exists():
        return json.dumps({"error": f"No project matching '{project_name}' found."})

    project = min(projects, key=lambda p: len(p.name))

    open_tasks = Task.objects.filter(project=project, status__in=[Task.TODO, Task.DOING])
    payload = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        for t in open_tasks
    ]
    return json.dumps({"project": project.name, "tasks": payload})


def mcp_search_tasks(user: DiscordUser, query: str) -> str:
    """MCP tool: search task titles across all the user's projects."""
    project_ids = ProjectMember.objects.filter(user=user).values_list('project_id', flat=True)
    tasks = Task.objects.filter(
        project_id__in=project_ids,
        title__icontains=query,
        status__in=[Task.TODO, Task.DOING],
    ).select_related('project')
    payload = [
        {
            "id": t.id,
            "title": t.title,
            "project": t.project.name,
            "status": t.status,
            "priority": t.priority,
        }
        for t in tasks
    ]
    return json.dumps(payload)


def handle_llm_action(actor: DiscordUser, action_dict: dict, channel_id: str) -> str:
    action = action_dict['action']
    data = action_dict['data']
    message = action_dict['message']

    try:
        if action == 'no_op':
            return message

        elif action == 'create_project':
            name = data.get('name', '').strip()
            description = data.get('description', '')
            if not name:
                return "I couldn't create the project — no name was provided."
            project = create_project(actor, name, description)
            tasks_data = data.get('tasks', [])
            for t in tasks_data:
                title = t.get('title', '').strip()
                if title:
                    add_task(actor, project, title)
            return message

        elif action == 'add_task':
            project_name = data.get('project_name', '').strip()
            title = data.get('title', '').strip()
            if not title:
                return "I couldn't add the task — no title was provided."
            if project_name:
                try:
                    project_ids = ProjectMember.objects.filter(user=actor).values_list('project_id', flat=True)
                    project = Project.objects.get(name=project_name, id__in=project_ids)
                except Project.DoesNotExist:
                    project = get_miscellaneous_project(actor)
            else:
                if channel_id:
                    try:
                        project = Project.objects.get(discord_channel_id=channel_id)
                    except Project.DoesNotExist:
                        project = get_miscellaneous_project(actor)
                else:
                    project = get_miscellaneous_project(actor)

            add_task(
                actor, project, title,
                description=data.get('description', ''),
                priority=int(data.get('priority', 3)),
                due_date=data.get('due_date'),
                deadline_type=data.get('deadline_type', 'soft'),
            )
            return message

        elif action == 'update_task':
            task_id = data.get('task_id')
            fields = data.get('fields', {})
            if not task_id:
                return "I couldn't update the task — no task ID was provided."
            update_task(actor, int(task_id), **fields)
            return message

        elif action == 'list_tasks':
            project_name = data.get('project_name', '').strip()
            project = None
            if project_name:
                try:
                    project_ids = ProjectMember.objects.filter(user=actor).values_list('project_id', flat=True)
                    project = Project.objects.get(name=project_name, id__in=project_ids)
                except Project.DoesNotExist:
                    return f"I couldn't find a project named '{project_name}'."
            tasks = list_tasks(actor, project)
            if not tasks:
                return "You have no open tasks."
            lines = [message] if message else []
            for task in tasks:
                due = f" (due {task.due_date.date()})" if task.due_date else ""
                lines.append(f"• [{task.status}] {task.title} — {task.project.name}{due}")
            return '\n'.join(lines)

        else:
            return f"Unknown action '{action}' — I can't handle that."

    except (PermissionError, ValueError) as e:
        return str(e)
    except Exception as e:
        return f"Something went wrong: {e}"


def process_message(discord_id: str, username: str, message_text: str, channel_id: str) -> tuple[str | None, bool]:
    user = get_or_create_discord_user(discord_id, username)

    if not user.is_authorized:
        return None, False

    tool_handlers = {
        "get_projects": lambda **_: mcp_get_projects(user),
        "get_tasks": lambda project_name, **_: mcp_get_tasks(user, project_name),
        "search_tasks": lambda query, **_: mcp_search_tasks(user, query),
    }

    start = time.monotonic()
    raw_response, elapsed_ms, tool_trace = ollama_client.chat(message_text, tool_handlers)
    raw_content = raw_response['message']['content']

    try:
        action_dict = ollama_client.parse_llm_response(raw_content)
    except ollama_client.LLMResponseParseError as e:
        log_payload = {'error': str(e), 'raw': raw_content}
        if tool_trace:
            log_payload['tool_calls'] = tool_trace
        MessageLog.objects.create(
            user=user,
            discord_channel_id=channel_id,
            message=message_text,
            llm_response=log_payload,
            elapsed_ms=elapsed_ms,
        )
        return "Sorry, I had trouble understanding the response from the AI.", False

    response_text = handle_llm_action(user, action_dict, channel_id)

    log_payload = dict(action_dict)
    if tool_trace:
        log_payload['tool_calls'] = tool_trace
    MessageLog.objects.create(
        user=user,
        discord_channel_id=channel_id,
        message=message_text,
        llm_response=log_payload,
        elapsed_ms=elapsed_ms,
    )

    needs_move_button = False
    if action_dict['action'] == 'add_task':
        misc = None
        try:
            misc = get_miscellaneous_project(user)
        except Project.DoesNotExist:
            pass
        if misc is not None:
            other_projects = [p for p in get_user_projects(user) if p.id != misc.id]
            needs_move_button = bool(other_projects)

    return response_text, needs_move_button
