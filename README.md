# project-bot

Project-Bot is a Discord bot that helps users manage projects and tasks through natural language. It uses a local LLM (via Ollama) with on-demand tool calling so context is fetched only when needed.

## Setup

**1. Create the virtualenv and install dependencies:**

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**2. Configure environment variables:**

```bash
cp .env.example .env
# Edit .env — set DJANGO_SECRET_KEY, DISCORD_BOT_TOKEN, DISCORD_ADMIN_IDS, etc.
```

**3. Apply database migrations:**

```bash
venv/bin/python manage.py migrate
```

**4. Create the Django admin superuser:**

```bash
# Set DJANGO_ADMIN_PASSWORD in .env first
venv/bin/python scripts/create_superuser.py
```

**5. Pull the Ollama model:**

```bash
ollama pull qwen3.5:9b
```

---

## Running

Use the helper script to start either service:

```bash
./start.sh django   # Django admin at http://127.0.0.1:8000/admin/
./start.sh bot      # Discord bot
```

### Django admin

The admin interface lets you manage users, projects, tasks, and inspect message logs.

- Authorize a Discord user via the `/authorize` slash command in Discord (requires `DISCORD_ADMIN_IDS` to be set), or directly in the admin under **Discord users**.
- The admin server can run independently of Ollama.

### Discord bot

The bot connects to Discord and processes messages in any channel it can read. Before starting, it validates that Ollama is running and `qwen3.5:9b` is available.

**Slash commands:**

| Command | Description |
|---|---|
| `/authorize @user` | Authorize a Discord user (admin only) |
| `/add_collaborator @user <project>` | Add a collaborator to a project |
| `/link_project <project>` | Link the current channel to a project |

**Natural language (in any channel):**

The bot responds to plain messages. Examples:
- `create a project called Website Redesign`
- `add a task to fix the login bug in Website Redesign, high priority`
- `mark task 5 as done`
- `list my tasks`

---

## Tests

```bash
# Fast tests (no Ollama required)
venv/bin/python manage.py test bot.tests_mcp

# Live LLM tests (requires Ollama + qwen3.5:9b)
venv/bin/python manage.py test bot.tests_llm --verbosity=2
```
