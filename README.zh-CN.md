# Prompt Copilot CLI

Prompt Copilot CLI 是一个轻量级的终端编程 Agent，适合本地开发场景。它将 OpenAI 兼容模型与一组实用工具结合起来，包括文件操作、命令执行、Python 脚本运行、多模态图片处理以及 MCP 工具扩展。

它适合希望在终端中进行交互式开发、查看项目结构、修改文件、运行命令的开发者。

## ✨ 功能特性

- 终端交互式 CLI
- 纯 ReAct 循环，配合内存中的对话上下文（保留最近 50 轮，程序退出即清空）
- 文件系统工具：读取、写入、删除、重命名、复制、递归列出目录、文本搜索、定向替换
- Python 脚本执行：前台运行，或以 `background` 参数以后台服务方式启动
- 支持视觉模型的图片处理：将图片读取并转成 base64
- MCP 工具集成，便于扩展外部能力
- 模型输出流式显示，并内联过滤 `<think>` 标签
- 可选的精简模式（`-b, --bare`）：不向上下文注入系统提示词与 function-calling 工具定义

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
  "mcp": {
    "enabled": true,
    "servers": []
  }
}
```

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

精简模式（不向上下文注入系统提示词与 function-calling 工具定义）：

```powershell
prompt-copilot -b
```

## 🧭 使用说明

### 交互命令

启动后可以使用以下命令：

- `/exit`：退出程序
- `/clear`：清空内存中的对话上下文

### 常用启动参数

```powershell
prompt-copilot -h
```

主要参数：

- `-v, --version`：打印版本号后退出
- `-t, --task`：一次性任务内容
- `-d, --workdir`：工作目录
- `-l, --lang`：语言（`zh` 或 `en`）
- `-rd, --request-delay`：模型请求之间的间隔秒数
- `-b, --bare`：精简模式，不向上下文注入系统提示词与 function-calling 工具定义

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
  - `search_code`（递归文本模式搜索）
  - `edit_file`（定向字符串替换）
- 执行工具
  - `execute_python_script`（前台运行，或通过 `background` 参数以后台服务方式启动）
- 多模态工具
  - `read_image_as_base64`

## 📁 项目结构

```text
.
├── main.py
├── requirements.txt
├── README.md
├── README.zh-CN.md
├── tests/
└── workspace/
```

## 🤝 参与贡献

欢迎提交 Issue、PR 或建议。如果你有新的功能想法、错误反馈或改进建议，欢迎一起参与。

## 📄 许可证

Apache License 2.0
