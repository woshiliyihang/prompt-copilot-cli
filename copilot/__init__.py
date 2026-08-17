"""Prompt Copilot CLI package.

Modules:
    i18n     — localization strings and helpers
    globals_ — paths, settings, interruption and token accounting
    prompts  — system prompts, working principles, task scratch memory
    config   — configuration loading and OpenAI client construction
    session  — persisted sliding-window conversation history
    memory   — SQLite + FTS5 long-term memory store
    tools    — built-in tool definitions and execution
    mcp      — MCP server discovery and invocation
    llm      — chat completion wrapper
    agent    — planning, tool loop and conversation recording
    cli      — argument parsing and interactive loop
"""
