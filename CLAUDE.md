# Claude Code Instructions

## Boundaries

Only read or modify files within the project directory (`/Users/david/source/dcw312/project-bot`). Do not access, read, or modify any files outside this directory.

## Python Environment

Always use the project venv for all Python commands:

```bash
venv/bin/python   # instead of python or python3
venv/bin/pip      # instead of pip or pip3
```

Never use system `python`, `python3`, `pip`, or `pip3` directly.

If the venv does not exist, create it first:

```bash
python3 -m venv venv
```
