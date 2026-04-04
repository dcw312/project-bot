# Project Clarification Questions

## LLM & Ollama

**Q1: Which Ollama model should be used by default?**
A:

**Q2: Should the system prompt and LLM response format be hardcoded, or configurable via settings/env vars?**
A:

**Q3: How should the system handle Ollama being unavailable or returning a malformed response — silent fallback, error message to user, or retry?**
A:

---

## Discord Bot

**Q4: Should the bot respond to all messages in a channel, or only to slash commands / direct mentions?**
A:

**Q5: Should the bot support multiple Discord servers (guilds), or is this single-server only?**
A:

---

## Data & Multi-tenancy

**Q6: Are projects and tasks scoped strictly per Discord user, or can users share/view each other's projects?**
A:

**Q7: Should `list_tasks` return tasks across all of a user's projects, or only for a specific project?**
A:

---

## API & Security

**Q8: Is the Django API internal-only (called only by the bot), or should it be designed to support other clients in the future?**
A:

**Q9: Should the API validate that requests come from the Discord bot (e.g. a shared secret / API key), or is auth out of scope entirely?**
A:

---

## Scope & Deployment

**Q10: What is the target environment — local dev only, or intended to run on a server (e.g. VPS, Docker)?**
A:
