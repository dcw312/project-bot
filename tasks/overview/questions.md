# Project Clarification Questions

## LLM & Ollama

**Q1: Which Ollama model should be used by default?**
A: `lfm2:latest` (LFM2-24B-A2B). Purpose-trained for agentic tool use and structured JSON output. `gpt-oss:20b` has known Ollama compatibility issues with structured output. `llama3.2` is a viable fallback for simple schemas only.

**Q2: Should the system prompt and LLM response format be hardcoded, or configurable via settings/env vars?**
A: Start with a template that is committed to the repository.

**Q3: How should the system handle Ollama being unavailable or returning a malformed response — silent fallback, error message to user, or retry?**
A: Return an error message to the user indicating that the service is temporarily unavailable.

---

## Discord Bot

**Q4: Should the bot respond to all messages in a channel, or only to slash commands / direct mentions?**
A: All messages in a channel.

**Q5: Should the bot support multiple Discord servers (guilds), or is this single-server only?**
A: Single-server only.

---

## Data & Multi-tenancy

**Q6: Are projects and tasks scoped strictly per Discord user, or can users share/view each other's projects?**
A: Projects and tasks are scoped strictly to a list of Discord users. One user owns the project but can add collaborators who are also Discord users.

**Q7: Should `list_tasks` return tasks across all of a user's projects, or only for a specific project?**
A: `list_tasks` should return tasks across all of a user's projects by default, with an option to filter by a specific project. This will be a significant area for future enhancement. For example, some tasks might only be done during a time of day (business hours, weekends, etc.). There will be a need to implement more advanced filtering and scheduling capabilities in the future.

---

## API & Security

**Q8: The bot now calls Django services directly (no HTTP). Should a REST API (`/api/chat/`) still be built as an optional interface for future external clients?**
A: No, not initially since this creates a security surface that is not needed for the bot's direct service calls.

**Q9: If the REST API is kept, should it require any authentication (e.g. API key), or is it treated as internal-only with no auth?**
A: N/A, do not implement authentication for the REST API initially since it is not being built.

---

## Scope & Deployment

**Q10: What is the target environment — local dev only, or intended to run on a server (e.g. VPS, Docker)?**
A: local development initially. Later we want to run in Docker container so that we can move the application to a dedicated server or cloud environment for production use.

---

## Follow-up Questions

**Q11: Should the Ollama model name be configurable via `.env` (e.g. `OLLAMA_MODEL=lfm2:latest`), or hardcoded alongside the system prompt template?**
A: It should be configurable and there should be an interface, implementation pattern to allow for a dedicated system prompt for different models if needed. The prompts should be stored in source and the application should error quickly if the configured model is not supported or if the prompt is missing. The prompts should be stored in a directory such as `prompts/<model_name>/` within the source code. Use the model name from the `.env` configuration to select the appropriate prompt directory and fail fast if it is not found.

**Q12: How are collaborators added to a project — via a Discord command (e.g. "add @user to project X")? And can collaborators create/update tasks, or is it view-only?**
A: There should be a slash command (e.g. `/add_collaborator @user to project X`) to add collaborators to a project. Collaborators should have permission to create and update tasks within the project, not just view them.

**Q13: Is `due_date` on `Task` sufficient to support future scheduling (e.g. time-of-day filtering), or should a scheduling concept be stubbed now (e.g. a `scheduled_for` datetime or a `recurrence` field)?**
A: `due_date` is sufficient and good to store one deadline for the initial implementation. In addition to a due_date, the task should have a `deadline_type` field to indicate whether the deadline is hard or soft, which can be useful for future scheduling and prioritization features.

**Q14: Which Discord channel(s) should the bot listen to — a specific channel name/ID set in `.env`, or all channels in the server?**
A: The bot should listen to all channels. In the future, channels might be used to scope projects, so listening to all channels provides flexibility for future enhancements.

**Q15: Ollama calls may be slow (several seconds). Should the bot send a typing indicator or acknowledgement message ("thinking…") while waiting for the LLM response?**
A: A thinking message should only be sent if the LLM call is taking more than 5 seconds (an env variable `LLM_THINKING_THRESHOLD` can be used to configure this). The bot should measure the elapsed time of the LLM call and send the typing indicator or acknowledgement message accordingly. This ensures that users receive feedback when the bot is processing a request, improving the user experience without spamming unnecessary messages for quick responses.

**Q16: Since the bot listens to all messages in all channels, how does it distinguish a message meant for the bot vs. general chat? Does every message get sent to the LLM, or is there a trigger (e.g. a prefix like `!`, or `@project-bot` mention)?**
A: It should process all messages except ones that start with a mention of someone else. In other words, if a message mentions the bot or does not mention any other user, it should be sent to the LLM for processing.

**Q17: If a user has multiple projects and sends an ambiguous message (e.g. "add a task to buy milk"), how should the LLM handle it — ask a follow-up question, pick the most recently active project, or require the user to always specify a project?**
A: If it is in the general channel, the LLM should add to the Miscellaneous project and respond back with a button to move it to one of the user's other projects.

**Q18: Is the `Activity` table strictly for internal audit/debugging, or should users be able to query their own history (e.g. "what did I do last week")?**
A: Users should be able to query their own activity history.

**Q19: Should collaborator membership be modelled as a formal `ProjectMember` table with a `role` field (e.g. `owner`, `collaborator`), or a simple M2M with the owner tracked separately on `Project`?**
A: Yes, let's model as a ProjectMember table with a `role` field to indicate whether the member is an `owner` or `collaborator`. The project should have a creator column that references who created the project and automatically assign that user as the `owner` in the ProjectMember table. The creator should be immutable once set.

**Q20: Should the app validate at startup that the configured `OLLAMA_MODEL` exists in the running Ollama instance (i.e. call `ollama list` or `/api/tags`), or is it sufficient to fail on the first LLM call?**
A: Yes, the app should validate at startup that the configured `OLLAMA_MODEL` exists in the running Ollama instance. This allows for early detection of misconfigurations and prevents runtime errors when the bot attempts to use an unsupported or nonexistent model.

**Q21: Can a collaborator add other collaborators to a project, or is that owner-only? Can the owner remove a collaborator?**
A: Yes, a collaborator can add other collaborators to a project, but only the owner can remove a collaborator.

**Q22: Should each project have its own dedicated Discord channel, or should users activate a project in general chat? Options: (a) bot creates a channel per project, (b) user manually links an existing channel to a project via slash command, (c) user sets an active project per session in any channel, (d) no channel scoping — always rely on explicit project names.**
A: Option (b): store an optional `discord_channel_id` on `Project`. A slash command `/link_project <name>` binds the current channel to a project. Messages in a linked channel are automatically scoped to that project with no ambiguity. General or unlinked channels fall back to the Miscellaneous behaviour (Q17). This is persistent across restarts, requires no per-session activation, does not require the bot to create or manage channels, and delivers cleanly on the Q14 future-scoping note.

**Q23: How should user authorization work — who can use the bot, and how are new users added?**
A: Access is restricted to authorized users only. Admins are defined as a comma-separated list of Discord user IDs in `.env` (`DISCORD_ADMIN_IDS`) and are never stored in the database. Admins authorize new users via `/authorize @user`, which sets `DiscordUser.is_authorized = True` and auto-creates a Miscellaneous project for that user. Messages from unauthorized users are silently ignored. The Miscellaneous project is a system-created default project, not one the user creates — it exists to catch unscoped tasks and always has the user as owner.
