# Prompt Copilot CLI

Prompt Copilot CLI is a lightweight terminal-based coding agent for local development workflows. It combines an OpenAI-compatible model with a set of practical tools for file operations, shell commands, Python execution, multimodal image handling, and MCP integrations.

It is designed for developers who want an interactive coding assistant that can inspect a workspace, edit files, run commands, and help turn multi-step conversations into a final, actionable prompt.

## ✨ Features

- Interactive CLI experience in the terminal
- Persistent session history and conversation logs
- File-system tools for reading, writing, deleting, renaming, copying, and recursive directory listing
- Shell command execution and Python script execution
- Image support for vision-capable models via image-to-base64 conversion
- MCP tool integration for extending the agent with external tools
- Task workflow with `/task-start` and `/task-end` to generate a polished final prompt

## 🚀 Quick Start

### 1. Install the package

Install it from PyPI:

```powershell
py -m pip install -U prompt-copilot-cli
```

The package installs a console command named `prompt-copilot`.

### 2. Configure the model

On first launch, the project creates a configuration file at:

- Windows: `%USERPROFILE%\.prompt-copilot\config.json`
- Linux/macOS: `~/.prompt-copilot/config.json`

Example:

```json
{
  "model": "gpt-4o-mini",
  "base_url": "http://127.0.0.1:11434/v1",
  "api_key": "dummy",
  "temperature": 0.2,
  "debug": false,
  "mcp": {
    "enabled": true,
    "servers": []
  }
}
```

### 3. MCP configuration (optional)

The agent can discover and use external MCP tools through the `mcp.servers` array. This is useful when you want to extend the agent with tools such as web search, filesystem helpers, or other local services.

Example configuration:

```json
{
  "mcp": {
    "enabled": true,
    "servers": [
      {
        "name": "bing",
        "command": "npx",
        "args": ["-y", "bing-cn-mcp"]
      },
      {
        "name": "open-websearch-http",
        "transport": "http",
        "url": "http://127.0.0.1:3000/mcp"
      }
    ]
  }
}
```

How it works:

- The first server uses a local stdio-based MCP server launched by `npx`.
- The second server connects to an HTTP MCP endpoint at the given URL.
- Once discovered, the tools exposed by these servers become callable by the agent during a session.
- If `enabled` is set to `false` or the server list is empty, no MCP tools will be loaded.

### 4. Run the agent


Normal node：

```powershell
prompt-copilot -d D:\project_dir
```

Interactive mode:

```powershell
prompt-copilot
```

One-off task mode:

```powershell
prompt-copilot -t "Create a simple HTML landing page" -d ./workspace -l en
```

## 🧭 Usage Guide

### Interactive commands

Once the CLI starts, you can use these commands:

- `/exit` — quit the program
- `/clear` — clear local session history
- `/task-start` — start a task context for later summarization
- `/task-end` — generate a final optimized prompt and save it to `last-prompt.md`

### Common startup options

```powershell
prompt-copilot -h
```

Key options:

- `-t, --task` — one-off task content
- `-d, --workdir` — working directory
- `-l, --lang` — language (`zh` or `en`)
- `-amc, --agent-messages-count` — number of messages kept in agent history
- `-rd, --request-delay` — delay between model requests in seconds
- `-hc, --history-count` — number of rounds kept in conversation history
- `--reset-session` — reset persisted session history

### Example workflows

#### 1. Ask the agent to inspect a project

```powershell
prompt-copilot -t "Inspect this repository and summarize the main structure" -d ./workspace
```

#### 2. Ask the agent to edit files and run tests

```powershell
prompt-copilot -t "Update the code, then run the relevant test suite" -d ./workspace
```

#### 3. Ask the agent to analyze an image

If your model supports vision, the agent can use the built-in image tool to read an image file and convert it to base64 for multimodal input.

Example prompt:

```text
Please inspect the image in ./workspace/demo.png and tell me what numbers or text are visible.
```

## 🛠 Tool capabilities

The agent can call the following tools:

- File tools
  - `read_file`
  - `write_file`
  - `delete_file`
  - `create_directory`
  - `delete_directory`
  - `rename_path`
  - `copy_file`
  - `list_dir` (with recursive option)
- Execution tools
  - `execute_command`
  - `execute_python_script`
- Multimodal tools
  - `read_image_as_base64`

## 🧠 Task flow

The project supports a lightweight task-iteration workflow:

1. Start a round with `/task-start`
2. Continue interacting with the agent to clarify requirements or refine the task
3. Finish with `/task-end`
4. The agent writes the final prompt to `last-prompt.md`

This is useful when you want to turn a long back-and-forth conversation into a compact, executable prompt.

## 📁 Project structure

```text
.
├── main.py
├── requirements.txt
├── README.md
├── README.zh-CN.md
├── tests/
└── workspace/
```

## 🤝 Contributing

Contributions are welcome. Please feel free to open an issue or submit a pull request if you have suggestions, bug reports, or new workflow ideas.

## 📄 License

Apache License 2.0
