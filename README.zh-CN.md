# Prompt Copilot CLI

一个刻意保持简单的终端编程 Agent，核心基于 **LangChain `create_agent` + LangGraph 持久化**。

这次不是在原架构上继续修补，而是直接推倒重来：删除自定义 OpenAI tool-call 循环、规划器、消息裁剪、SessionStore、MCP 核心层和自研长期记忆，实现真正由 LangChain/LangGraph 负责 Agent 编排与上下文管理。

## 核心架构

```text
CLI
 └── AgentRuntime
      ├── ChatOpenAI                 # OpenAI 兼容接口模型
      ├── create_agent()             # Agent + Tool Loop
      ├── SummarizationMiddleware    # 自动压缩长上下文
      ├── SqliteSaver                # 持久化会话状态
      ├── SqliteStore                # 持久化长期记忆
      └── LangMem tools              # 长期记忆搜索/管理

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

短期记忆和长期记忆明确分离：LangGraph `checkpointer` 保存当前线程的完整 Agent 状态；LangGraph `store` 保存跨会话长期信息。上下文过长时，LangChain `SummarizationMiddleware` 自动总结旧消息，而不是继续维护原来的 `max_messages` 滑动窗口。

## 为什么要这样重构

- **不再自己实现 ReAct/tool loop**：直接使用 `create_agent()`。
- **不再手工裁剪消息**：使用 `SummarizationMiddleware` 管理长上下文。
- **不再维护 SessionStore**：使用 `SqliteSaver` 持久化 Agent thread。
- **不再维护 SQLite + FTS5 自研记忆系统**：使用 `SqliteStore` + LangMem。
- **保留 OpenAI API 风格模型调用**：`ChatOpenAI` 支持 `model/api_key/base_url`。
- **工具极简**：只保留编程 Agent 真正需要的文件、搜索、编辑、脚本、命令能力。
- **后续可平滑升级**：SQLite 只是当前本地后端，未来可直接换成 `PostgresSaver + PostgresStore`。

## 安装

```powershell
py -m pip install -U prompt-copilot-cli
```

开发模式：

```powershell
py -m pip install -e .
```

## 模型配置

第一次运行会生成：

- Windows：`%USERPROFILE%\\.prompt-copilot\\config.json`
- Linux/macOS：`~/.prompt-copilot/config.json`

示例：

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

只要服务实现 OpenAI Chat Completions 兼容接口，就可以通过 `base_url` 接入。例如本地模型、OpenRouter、vLLM 或其他 OpenAI-compatible 服务都可以按其兼容程度使用。

## 启动

交互模式：

```powershell
prompt-copilot -d D:\project
```

单次任务：

```powershell
prompt-copilot -d D:\project -t "检查项目代码，修复测试失败，并运行相关测试。"
```

交互命令：

- `/exit`：退出
- `/clear`：删除当前 thread，重新开始上下文
- `/memory`：查看当前工作区的长期记忆

CLI 支持多行输入：`Enter` 换行，`Ctrl+Enter` 提交。

## 工具

只保留编程 Agent 必要的简单工具：

- `read_file`：读取文件
- `list_files`：查看目录结构
- `search_code`：搜索代码
- `write_file`：创建/完整覆盖文件
- `edit_file`：精确文本编辑
- `delete_file`：删除文件或空目录
- `execute_python_script`：执行 Python 脚本
- `execute_command`：执行命令

新架构不再包含 MCP、图片工具、自定义规划器、自定义 OpenAI tool-call 协议等核心复杂度。

## 长期记忆与上下文

### 短期上下文

`SqliteSaver` 持久化 Agent 当前 thread 的完整状态。程序退出后重新启动，同一个工作区仍然可以继续之前的上下文。

当对话不断增长时，`SummarizationMiddleware` 会自动总结旧消息并保留最近消息，避免原项目中手工 `max_messages` 截断导致上下文断裂、tool call/tool result 配对问题。

### 长期记忆

`SqliteStore` 负责跨会话长期存储，LangMem 提供：

- `search_memory`
- `manage_memory`

Agent 可以在任务过程中主动记录项目决策、用户偏好、重要约定和可复用经验，也可以搜索以前的长期记忆。

每次新请求开始时，运行时还会主动召回相关长期记忆，再交给 Agent，因此不是单纯“把所有历史塞进上下文”。

默认数据库：

```text
~/.prompt-copilot/memory.db
```

后续如果需要多进程、高并发或服务化，可以直接把后端换成 LangGraph 官方的 PostgreSQL Store/Checkpointer，不需要重新设计 Agent。

## 项目结构

```text
.
├── main.py
├── copilot/
│   ├── __init__.py
│   ├── agent.py       # LangChain create_agent 组合层
│   ├── cli.py         # CLI
│   ├── config.py      # 配置
│   ├── memory.py      # LangGraph SQLite + LangMem
│   └── tools.py       # 编程工具
├── tests/
│   ├── test_config.py
│   └── test_tools.py
└── pyproject.toml
```

## 测试

```powershell
python -m pytest
```

测试也已经按照新架构重新整理，不再保留旧 Agent loop、旧 SessionStore、旧 FTS5 memory 等实现的测试。

## License

Apache License 2.0
