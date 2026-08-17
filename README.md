# Prompt Copilot CLI

A deliberately small terminal coding agent built on **LangChain `create_agent` + LangGraph persistence**.

The previous implementation maintained its own OpenAI tool-call loop, message trimming, planning layer, MCP runtime, session store, and memory engine. This version removes that orchestration code and delegates agent execution and context management to LangChain/LangGraph.

## Architecture

```text
CLI
 └── AgentRuntime
      ├── ChatOpenAI                 # OpenAI-compatible endpoint
      ├── create_agent()             # model/tool loop
      ├── SummarizationMiddleware    # automatic short-term context compression
      ├── SqliteSaver                # durable thread state
      ├── SqliteStore                # durable long-term memory
      └── LangMem tools              # search/manage persistent memories

Tools
 ├── read_file
 ├── list_files
 ├── search_code
 ├── write_file
 ├── edit_file
 ├── delete_file
 ├── execute_python_script
 └── execute_command
```

LangChain's current agent API provides the model/tool loop, while LangGraph separates short-term thread state (`checkpointer`) from long-term memory (`store`). The built-in `SummarizationMiddleware` compresses old conversation messages when the configured token threshold is reached. LangMem provides the memory search/manage tools on top of the LangGraph store.

## Why this architecture

- **No handwritten ReAct/tool loop**: `create_agent()` owns the loop.
- **No manual message slicing**: `SummarizationMiddleware` manages long conversations.
- **No custom session database**: `SqliteSaver` persists the agent thread.
- **No custom FTS memory implementation**: `SqliteStore` + LangMem provide the long-term memory abstraction.
- **OpenAI-compatible models**: `ChatOpenAI` accepts `model`, `api_key`, `base_url`, temperature, timeout, and retries.
- **Small tool surface**: coding operations are exposed as normal LangChain tools.
- **Easy future upgrade**: SQLite is the local backend; the LangGraph store/checkpointer interfaces can later be moved to Postgres without changing the agent layer.

## Install

```powershell
py -m pip install -U prompt-copilot-cli
```

For development:

```powershell
py -m pip install -e .
```

## Configuration

The first run creates:

- Windows: `%USERPROFILE%\\.prompt-copilot\\config.json`
- Linux/macOS: `~/.prompt-copilot/config.json`

Example for an OpenAI-compatible service:

```json
{
  "model": "gpt-4o-mini",
  "base_url": "https://api.openai.com/v1",
  "api_key": "YOUR_API_KEY",
  "temperature": 0.2,
  "timeout": 120,
  "memory": {
    "enabled": true,
    "max_recent_memories": 5
  },
  "context": {
    "summary_trigger_tokens": 12000,
    "keep_messages": 20
  }
}
```

Any OpenAI-compatible Chat Completions endpoint can be configured through `base_url`. LangChain's `ChatOpenAI` documentation notes that custom `base_url` is intended for providers implementing the OpenAI API specification.

## Run

Interactive:

```powershell
prompt-copilot -d D:\project
```

One-shot task:

```powershell
prompt-copilot -d D:\project -t "Inspect the repository, fix the failing tests, and run the relevant test suite."
```

Useful commands:

- `/exit` — quit
- `/clear` — delete the current thread and start a new one
- `/memory` — show persistent memories for the workspace

The CLI supports multiline input. `Enter` inserts a newline and `Ctrl+Enter` submits the request.

## Tools

The agent intentionally exposes only a small coding-oriented tool set:

- `read_file` — read UTF-8 source files
- `list_files` — inspect the workspace tree
- `search_code` — search source text
- `write_file` — create/replace a file
- `edit_file` — exact one-match text replacement
- `delete_file` — delete a file or empty directory
- `execute_python_script` — execute Python in the workspace
- `execute_command` — execute a shell command in the workspace

There is no MCP layer, image tool, custom planner, or custom OpenAI tool-call protocol in the new core architecture.

## Memory and context

There are two different persistence layers:

### Short-term conversation memory

LangGraph's `SqliteSaver` persists the complete agent state for a workspace thread. The same workspace resumes the same thread after restarting the CLI.

When the conversation grows, LangChain's `SummarizationMiddleware` automatically summarizes older messages and keeps a recent message window. This avoids the old `max_messages` tail-slicing logic and, importantly, lets LangChain preserve tool-call/message relationships correctly.

### Long-term memory

LangGraph's `SqliteStore` stores durable JSON memory entries under a workspace namespace. LangMem adds `search_memory` and `manage_memory` tools so the agent can explicitly remember, update, delete, and search durable project/user context.

The runtime also recalls relevant durable memories before each new request. This gives the agent both automatic contextual recall and explicit memory-management tools.

Storage defaults to:

```text
~/.prompt-copilot/memory.db
```

The storage implementation is deliberately behind LangGraph's standard interfaces. For a multi-process production deployment, the next backend can be `PostgresSaver` + `PostgresStore` without rebuilding the agent loop.

## Project structure

```text
.
├── main.py
├── copilot/
│   ├── __init__.py
│   ├── agent.py       # LangChain create_agent composition
│   ├── cli.py         # terminal UX and commands
│   ├── config.py      # small JSON configuration loader
│   ├── memory.py      # LangGraph SQLite + LangMem
│   └── tools.py       # coding tools
├── tests/
│   ├── test_config.py
│   └── test_tools.py
└── pyproject.toml
```

## Testing

```powershell
python -m pytest
```

The tests intentionally target the new small modules instead of preserving tests for the removed architecture.

## License

Apache License 2.0
