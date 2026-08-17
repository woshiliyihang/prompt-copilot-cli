# Prompt Copilot CLI

Prompt Copilot CLI 是一个轻量级的终端编程 Agent，适合本地开发场景。它将 OpenAI 兼容模型与一组实用工具结合起来，包括文件操作、命令执行、Python 脚本运行、多模态图片处理、MCP 工具扩展，以及基于 SQLite 的生产级长期记忆系统。

它适合希望在终端中进行交互式开发、查看项目结构、修改文件、运行命令、跨会话记住重要信息，并把多轮对话整理成最终可执行提示词的开发者。

## ✨ 功能特性

- 终端交互式 CLI
- 持久化会话历史与对话记录
- **长期记忆**：SQLite + FTS5 全文检索（支持中文），任务开始前自动检索注入相关记忆，任务结束后可自动提炼沉淀
- 文件系统工具：读取、写入、删除、重命名、复制、递归列出目录
- 命令执行（默认后台执行并支持健康检查）与 Python 脚本执行
- 支持视觉模型的图片处理：将图片读取并转成 base64
- MCP 工具集成，便于扩展外部能力
- 支持 `/task-start` 和 `/task-end` 的任务流，生成最终优化提示词

## 🚀 快速开始

### 1. 安装软件包

从 PyPI 安装：

```powershell
py -m pip install -U prompt-copilot-cli
```

安装后会提供一个命令行入口 `prompt-copilot`。

### 2. 配置模型

首次运行时，项目会在如下位置生成配置文件：

- Windows: `%USERPROFILE%\.prompt-copilot\config.json`
- Linux/macOS: `~/.prompt-copilot/config.json`

示例配置：

```json
{
  "model": "gpt-4o-mini",
  "base_url": "http://127.0.0.1:11434/v1",
  "api_key": "dummy",
  "temperature": 0.2,
  "debug": false,
  "memory": {
    "enabled": true,
    "auto_extract": true,
    "max_results": 3
  },
  "mcp": {
    "enabled": true,
    "servers": []
  }
}
```

记忆配置说明：

- `memory.enabled`：是否启用长期记忆系统及其工具
- `memory.auto_extract`：任务结束后是否由模型自动提炼可复用记忆
- `memory.max_results`：每次任务前注入上下文的相关记忆条数

### 3. MCP 配置（可选）

Agent 可以通过 `mcp.servers` 数组发现并调用外部 MCP 工具。这个能力适合接入网页搜索、外部服务、辅助工具等扩展能力。

配置示例：

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

说明：

- 第一个示例是通过 `npx` 启动一个本地 stdio 类型的 MCP 服务。
- 第二个示例是连接到一个 HTTP 类型的 MCP 服务端点。
- 一旦这些服务被发现，Agent 就可以在会话中调用它们暴露出来的工具。
- 如果 `enabled` 设为 `false`，或者 `servers` 为空，则不会加载任何 MCP 工具。

### 4. 启动 Agent

标准模式：

```powershell
prompt-copilot -d D:\project_dir -l zh
```

交互模式：

```powershell
prompt-copilot
```

单次任务模式：

```powershell
prompt-copilot -t "创建一个简单的 HTML 落地页" -d D:\project_dir -l zh
```

## 🧭 使用说明

### 交互命令

启动后可以使用以下命令：

- `/exit`：退出程序
- `/clear`：清空本地会话历史
- `/task-start`：开始任务上下文
- `/task-end`：生成最终优化提示词并写入 `last-prompt.md`
- `/memory`：列出最近的长期记忆条目

### 常用启动参数

```powershell
prompt-copilot -h
```

主要参数：

- `-t, --task`：一次性任务内容
- `-d, --workdir`：工作目录
- `-l, --lang`：语言（`zh` 或 `en`）
- `-amc, --agent-messages-count`：保留在 Agent 历史中的消息数量
- `-rd, --request-delay`：模型请求之间的间隔秒数
- `-hc, --history-count`：会话历史保留的轮次数量
- `--reset-session`：重置本地持久化会话记录

### 示例场景

#### 1. 检查项目结构

```powershell
prompt-copilot -t "检查这个仓库并总结主要结构" -d ./workspace
```

#### 2. 编辑文件并执行测试

```powershell
prompt-copilot -t "修改代码并运行相关测试" -d ./workspace
```

#### 3. 分析图片内容

如果你的模型支持视觉能力，Agent 可以调用图片工具读取图片并将其转换成 base64 供多模态模型分析。

例如：

```text
请看一下 ./workspace/demo.png 中能看到哪些数字或文字。
```

## 🛠 工具能力

Agent 可以调用这些工具：

- 文件工具
  - `read_file`
  - `write_file`
  - `delete_file`
  - `create_directory`
  - `delete_directory`
  - `rename_path`
  - `copy_file`
  - `list_dir`（支持递归）
- 执行工具
  - `execute_command`（默认后台执行；`background=false` 时同步等待并返回退出码；可通过 `health_check_url` 轮询就绪状态）
  - `execute_python_script`
- 记忆工具
  - `memory_search`
  - `memory_add`
  - `memory_list`
  - `memory_delete`
- 多模态工具
  - `read_image_as_base64`

## 🧠 长期记忆

Agent 维护一个生产级的长期记忆库，位于 `~/.prompt-copilot/memory.db`：

- **存储**：SQLite 表存储简洁、自包含的记忆条目（类型 + 内容 + 时间），写入时自动去重。
- **检索**：FTS5 全文排序（bm25），中文按字切分以支持 CJK 匹配，并带 `LIKE` 兜底；每次任务前将最相关的 `memory.max_results` 条记忆注入上下文。
- **安全**：疑似包含密钥、密码、私钥、Bearer Token 的内容，或超过 300 字的内容，一律拒绝写入。
- **自动提炼**：当 `memory.auto_extract` 开启时，任务结束后由模型从对话记录中提炼可复用记忆（事实、偏好、决策、经验）。
- **管理**：用 `/memory` 查看条目，任务内可使用 `memory_*` 工具增删查改；支持按 ID 删除。

## 🧠 任务流

项目支持一种轻量的任务迭代流程：

1. 用 `/task-start` 开始一轮任务
2. 与 Agent 连续沟通，澄清需求或修正目标
3. 用 `/task-end` 结束任务
4. 最终提示词被写入 `last-prompt.md`

这对于将多轮对话整理成一条可直接执行的提示词非常有帮助。

## 📁 项目结构

```text
.
├── main.py               # 薄入口，重新导出公共 API
├── copilot/              # 实现包
│   ├── i18n.py           # 翻译与 t()
│   ├── globals_.py       # 路径、设置、中断状态、token 统计
│   ├── prompts.py        # 工作原则、规划提示词、任务记忆
│   ├── config.py         # 配置加载、校验、客户端构建
│   ├── session.py        # 持久化滑动窗口历史
│   ├── memory.py         # SQLite + FTS5 长期记忆库
│   ├── tools.py          # 工具定义与执行
│   ├── mcp.py            # MCP 服务发现与调用
│   ├── llm.py            # 模型调用封装
│   ├── agent.py          # 规划、工具循环、对话记录
│   └── cli.py            # 参数解析与交互循环
├── tests/
└── workspace/
```

## 🤝 参与贡献

欢迎提交 Issue、PR 或建议。如果你有新的功能想法、错误反馈或改进建议，欢迎一起参与。

## 📄 许可证

Apache License 2.0
