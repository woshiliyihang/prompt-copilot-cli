# Copilot instructions for prompt-copilot-cli

## Architecture

This repository is intentionally small. Do not reintroduce a handwritten agent loop, custom session store, custom message trimming, MCP runtime, or a second memory abstraction.

- `main.py`: thin console entry point.
- `copilot/cli.py`: terminal UX and CLI commands.
- `copilot/agent.py`: composition root for `ChatOpenAI`, LangChain `create_agent`, middleware, tools, checkpointer, and store.
- `copilot/tools.py`: only coding tools.
- `copilot/memory.py`: LangGraph SQLite persistence and LangMem tools.
- `copilot/config.py`: minimal JSON configuration.

## Agent design rules

1. Use LangChain `create_agent()` for the model/tool loop.
2. Use LangGraph `checkpointer` for short-term thread persistence.
3. Use LangGraph `store` and LangMem for long-term memory.
4. Use `SummarizationMiddleware` for long-context compression.
5. Keep tools small, deterministic where practical, and return useful textual evidence.
6. OpenAI-compatible providers are configured with `model`, `api_key`, and `base_url`.
7. Do not add MCP, vector databases, custom ReAct loops, or custom message-window logic unless explicitly requested.

## Tools

The supported coding tool set is:

- `read_file`
- `list_files`
- `search_code`
- `write_file`
- `edit_file`
- `delete_file`
- `execute_python_script`
- `execute_command`

## Development

Install:

```text
python -m pip install -e .
```

Run tests:

```text
python -m pytest
```

Run the CLI against a workspace:

```text
prompt-copilot -d <workspace>
```

The console entry point is `main:main`.

## Persistence

The local runtime uses SQLite for simplicity. `SqliteSaver` stores thread state and `SqliteStore` stores long-term memories in `~/.prompt-copilot/memory.db`.

If a future deployment requires multiple processes or higher concurrency, prefer LangGraph's PostgreSQL saver/store rather than inventing another persistence layer.
