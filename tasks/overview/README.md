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

- Discord bot (Python)
- Django backend (API + ORM + admin)
- Ollama LLM integration
- Postgres database

**Flow:**

```
Discord → Bot → Django API → Ollama → Django (tool execution) → Response → Discord
```

## 🗃️ Data Model (Django)

Implement the following models:

**DiscordUser**
- `discord_id` (string, unique)
- `username` (string)

**Project**
- `name` (string)
- `description` (text, optional)
- `owner` (FK to DiscordUser)
- `created_at`

**Task**
- `project` (FK)
- `title` (string)
- `description` (text, optional)
- `status` (todo, doing, done)
- `priority` (int, default 3)
- `due_date` (datetime, optional)
- `created_at`

**Activity**
- `user` (FK)
- `action` (string)
- `payload` (JSON)
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

## 🌐 API Design

**Endpoint:** `POST /api/chat/`

**Request:**

```json
{
  "discord_id": "…",
  "username": "…",
  "message": "…"
}
```

**Response:**

```json
{
  "message": "…",
  "data": {"…optional structured data…"}
}
```

## 🤖 Discord Bot

Requirements:

- Use slash commands or message listener
- Forward user message to Django API
- Display response message
- No business logic in bot

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
  - serializers (or validation layer)
  - API view for `/api/chat/`
  - admin setup
- Ollama integration module
- Discord bot script
- Example system prompt builder
- Example LLM response parser
- README with:
  - setup instructions
  - how to run Ollama
  - how to run bot + server

## ⚡ Non-goals (do NOT implement)

- Authentication beyond Discord ID
- Complex permissions
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
