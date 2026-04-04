You are a senior software engineer. Build a minimal but production-sane system with the following requirements.

## 🎯 Goal

Create a Discord-driven AI task manager using Django and Ollama.

The system must:

- Accept messages from a Discord bot
- Send them to a Django backend
- Use an LLM (via Ollama) to interpret intent
- Store and manage projects and tasks in a database
- Return a natural language response back to Discord

Do NOT implement any system command execution (no shell, SSH, etc). This is strictly a task/project management system.

## 🧩 Architecture

**Components:**

- Django backend (ORM + admin + service layer)
- Discord bot — runs as a Django management command (`python manage.py run_bot`), makes direct Python calls into Django services (no HTTP)
- Ollama LLM integration
- Postgres database

**Flow:**

```
Discord → Bot (management command) → Django services (direct call) → Ollama → Django ORM → Response → Discord
```

The bot lives inside the Django project and imports service functions directly. There is no internal HTTP round-trip. Django admin and the REST API (if kept) serve as optional interfaces — not required by the bot.

**Running the system:**

```
# Terminal 1 — Django dev server (admin + optional API)
python manage.py runserver

# Terminal 2 — Discord bot
python manage.py run_bot
```

## 🔑 Authorization

Access to the bot is restricted to authorized Discord users.

- **Admins** are defined as a comma-separated list of Discord user IDs in `.env` (`DISCORD_ADMIN_IDS`). They are never stored in the database — the env var is the source of truth.
- **Authorized users** are stored in the database (`DiscordUser.is_authorized`). Only admins can authorize users via the slash command `/authorize @user`.
- Messages from unauthorized users are silently ignored.
- When a user is first authorized, a **Miscellaneous** project is automatically created for them. This is the default project for unscoped tasks.

## 🗃️ Data Model (Django)

Implement the following models:

**DiscordUser**
- `discord_id` (string, unique)
- `username` (string)
- `is_authorized` (bool, default False)
- `authorized_at` (datetime, optional)
- `created_at`

**Project**
- `name` (string)
- `description` (text, optional)
- `creator` (FK to DiscordUser, immutable)
- `discord_channel_id` (string, optional) — links this project to a Discord channel
- `created_at`

**ProjectMember**
- `project` (FK)
- `user` (FK to DiscordUser)
- `role` (owner, collaborator)

**Task**
- `project` (FK)
- `title` (string)
- `description` (text, optional)
- `status` (todo, doing, done)
- `priority` (int, default 3)
- `due_date` (datetime, optional)
- `deadline_type` (hard, soft)
- `created_at`

**Activity** *(user-facing — records task/project change events)*
- `user` (FK)
- `action` (string)
- `payload` (JSON)
- `created_at`

**MessageLog** *(internal/debug — raw Discord messages and LLM responses)*
- `user` (FK)
- `discord_channel_id` (string)
- `message` (text)
- `llm_response` (JSON)
- `elapsed_ms` (int)
- `created_at`

Register all models in Django admin.

## 🤖 LLM Integration (Ollama)

Use Ollama's `/api/chat` endpoint.

Each request must include:

- system prompt (constructed dynamically)
- user message
- relevant project/task context (summarized)

## 🧠 LLM Behavior Design

The LLM must NOT directly modify the database.

Instead, it returns structured JSON describing an action.

**Supported actions:**

- `create_project`
- `add_task`
- `update_task`
- `list_tasks`
- `no_op` (if just responding conversationally)

**Example response:**

```json
{
  "action": "create_project",
  "data": {
    "name": "Garage Renovation",
    "tasks": [
      {"title": "Clean garage"},
      {"title": "Plan shelving"}
    ]
  },
  "message": "I created a new project with starter tasks."
}
```

## ⚙️ Backend Responsibilities

- Validate LLM output strictly
- Execute allowed actions only
- Write changes to database
- Log all actions in Activity table
- Return user-friendly message
- Reject malformed or unsafe responses

## 🤖 Discord Bot

Runs as a Django management command (`manage.py run_bot`).

Requirements:

- Listen to all messages in all channels
- Ignore messages from unauthorized users silently
- Ignore messages that start with a mention of another user (not the bot)
- Call Django service functions directly (no HTTP)
- Display response message
- No business logic in the bot — delegate everything to the service layer

**Slash commands:**
- `/authorize @user` — admin only, authorizes a Discord user
- `/add_collaborator @user <project>` — adds a collaborator to a project
- `/link_project <project>` — links the current channel to a project

## 🧠 System Prompt Requirements

Construct dynamically and include:

- **Instruction:** "You are a project/task planning assistant"
- **Rules:**
  - Only use supported actions
  - Prefer small actionable tasks
  - Do not hallucinate unknown projects
- Include current projects and tasks (summarized)

## 🔐 Safety Constraints

- Never execute arbitrary code
- Never trust LLM output without validation
- Enforce strict schema validation
- Scope data to the requesting user

## 🚀 Deliverables

Produce:

- Django project with:
  - `models.py`
  - `services.py` — business logic callable by the bot directly
  - validation layer for LLM output
  - admin setup
- `management/commands/run_bot.py` — Discord bot as a management command
- Ollama integration module
- Example system prompt builder
- Example LLM response parser
- README with:
  - setup instructions
  - how to run Ollama
  - how to run bot + Django admin

## ⚡ Non-goals (do NOT implement)

- Complex role-based permissions beyond owner/collaborator
- Background workers
- UI frontend
- Deployment configs

## 💡 Implementation Guidance

- Keep code simple and readable
- Prefer explicit over abstract
- Use function-based structure over premature architecture
- Stub where necessary but keep flow complete

## ✅ Success Criteria

- User sends message in Discord
- Task/project is created or updated via LLM
- Response is returned conversationally
- Data persists correctly in DB
- System is easy to extend later

Start with a working vertical slice, not a perfect system.
