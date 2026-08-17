# Changelog

All notable changes to this project are documented in this file.

## Unreleased

## 0.3.0

- Refactored the single-file `main.py` (2100+ lines) into the `copilot` package: `i18n`, `globals_`, `prompts`, `config`, `session`, `memory`, `tools`, `mcp`, `llm`, `agent`, `cli`; `main.py` remains a thin entry point that re-exports the public API
- Added a production-grade long-term memory system backed by SQLite + FTS5: durable storage at `~/.prompt-copilot/memory.db`, hybrid full-text (bm25) retrieval with CJK-friendly per-character segmentation plus a LIKE fallback, duplicate detection, secret and oversize rejection, and `/memory` plus `memory_search` / `memory_add` / `memory_list` / `memory_delete` tools
- Relevant long-term memories are now retrieved and injected into the context before each task, and memories can be auto-extracted from completed tasks
- Restored the `execute_command` tool with background-by-default execution, `start_background_process`, and health-check polling; short commands run synchronously with a sentinel-based exit code and a watchdog so silent commands cannot hang the agent
- Restored bilingual working principles (`DEFAULT_SYSTEM_WORKING_PRINCIPLES_ZH` / `_EN`) and planning prompt guidance for command classification
- Fixed context-window trimming: `trim_messages_window` no longer orphans tool-result messages from their assistant tool-call messages
- Added a maximum tool-loop iteration cap so a model that keeps requesting tools cannot run forever
- Fixed `ensure_config` to stop mutating the global default configuration on load
- `execute_python_script` now writes unique temp scripts per invocation and always cleans them up
- Added tests for the memory store, memory tools, and message trimming

## Unreleased (prior)

- Tool-call stage display now shows assistant reasoning instead of tool name and arguments
- Task memory now records assistant reasoning when tool calls are initiated
- Removed dead commented-out recorder and history-injection code
- Fixed license metadata in `pyproject.toml` to match the Apache-2.0 LICENSE file

## 0.2.9

- Added task long-term memory: tool invocations are persisted to a local memory file for context recovery across rounds
- Consolidated bilingual system prompts into a single unified prompt; removed redundant English translation variants
- Adjusted default retained chat count from 7 to 6 to fit the memory-based context strategy

## 0.2.8

- Added `search_code` tool for recursive text pattern search within directories
- Added `edit_file` tool for targeted string replacement in files
- Planning phase now uses a structured step-by-step format and runs without tool calls
- Task-end prompt generation now runs without tool calls, with fallback extraction from tool-call arguments

## 0.2.6

- Extracted shared working principles into reusable constants used by system, planning, and task-end prompts
- Planning phase now explicitly forbids executing commands, scripts, or tools
- Fixed English working-principles numbering format

## 0.2.3

- Consolidated command execution into `execute_python_script` with a `timeout_seconds` parameter (default 120s, max 360s)
- Added cross-platform process-group isolation and full process-tree cleanup on timeout or interrupt
- Added streaming line-by-line output for synchronous command execution
- Long-running tasks now default to background execution with heartbeat logging

## 0.2.2

- Metadata-only release; no user-visible changes

## 0.2.1

- Added `get_background_process_status` for checking background processes and retrieving recent output
- Background process output is now drained through a pipe-based daemon thread instead of direct file writes
- Tool results now display the tool description alongside name and output
- Raised task-end prompt length limit from 2000 to 18000 characters

## 0.1.9

- Added `timeout_seconds` and `output_log_path` parameters to command execution
- Added real-time streaming of background process output via log tailing
- Fixed `read_file` crash on binary/non-UTF-8 files; now returns a clear error message
- Special commands (`/exit`, `/clear`, `/task-start`, `/task-end`) now skip the planning step

## 0.1.8

- Added background process support with automatic detection and health-check polling
- Added an LLM planning step that decomposes user requests into a structured checklist
- Raised default retained chat history from 5 to 7
- Reduced subprocess timeout from 1 hour to 6 minutes

## 0.1.6

- First PyPI release with CLI argument parsing, interactive mode, and single-task mode
- Added file tools (read/write) and command execution with configurable timeout
- Added MCP transport support (stdio and HTTP)
- Added multimodal image input handling
- Added bilingual (English/Chinese) documentation and a release automation script

## 0.1.1

- Added PyPI-friendly packaging metadata
- Added Apache-2.0 license file
- Added entry point for `prompt-copilot`
- Improved documentation for installation and usage
