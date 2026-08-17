"""Localization support for Prompt Copilot CLI.

All user-facing strings live in :data:`TRANSLATIONS`.  The active language is
stored in the module-level :data:`UI_SYSTEM_LANGUAGE` variable; use
:func:`set_language` to change it so every consumer sees the update.
"""
from __future__ import annotations

from typing import Any

UI_SYSTEM_LANGUAGE = "en"


def set_language(language: str) -> None:
    """Set the UI language used by :func:`t`."""
    global UI_SYSTEM_LANGUAGE
    UI_SYSTEM_LANGUAGE = str(language or "en").strip() or "en"


def get_language() -> str:
    return UI_SYSTEM_LANGUAGE


TRANSLATIONS: dict[str, dict[str, str]] = {
    "config_field_model": {"zh": "模型名称，例如 qwen2.5-7b-instruct", "en": "Model name, for example qwen2.5-7b-instruct"},
    "config_field_base_url": {"zh": "OpenAI 兼容接口地址，例如 http://127.0.0.1:11434/v1", "en": "OpenAI-compatible base URL, for example http://127.0.0.1:11434/v1"},
    "config_field_api_key": {"zh": "API Key；若是本地模型可填写任意非空字符串", "en": "API key; for a local model you can enter any non-empty string"},
    "config_field_temperature": {"zh": "采样温度，取值通常在 0.0 到 1.0 之间", "en": "Sampling temperature, usually between 0.0 and 1.0"},
    "config_field_debug": {"zh": "是否开启调试日志，建议保持 true", "en": "Whether to enable debug logging; true is recommended"},
    "config_field_mcp": {"zh": "MCP 工具服务器配置对象，包含 enabled 和 servers", "en": "MCP tool-server configuration object containing enabled and servers"},
    "config_field_memory": {"zh": "长期记忆配置对象，包含 enabled / auto_extract / max_results", "en": "Long-term memory configuration object containing enabled / auto_extract / max_results"},
    "read_file_desc": {"zh": "读取指定文件内容（文本模式）。", "en": "Read the contents of the specified file in text mode."},
    "write_file_desc": {"zh": "写入或覆盖指定文件内容。", "en": "Write or overwrite the content of the specified file."},
    "delete_file_desc": {"zh": "删除指定文件。", "en": "Delete the specified file."},
    "create_directory_desc": {"zh": "创建目录（支持递归创建）。", "en": "Create a directory (supports recursive creation)."},
    "delete_directory_desc": {"zh": "删除目录及其内容。", "en": "Delete a directory and its contents."},
    "rename_path_desc": {"zh": "重命名或移动文件/目录。", "en": "Rename or move a file or directory."},
    "copy_file_desc": {"zh": "复制文件。", "en": "Copy a file."},
    "read_image_as_base64_desc": {"zh": "读取图片文件并将其编码为 base64。", "en": "Read an image file and encode it as base64."},
    "list_dir_desc": {"zh": "列出目录中的文件和子目录。", "en": "List files and subdirectories in the given directory."},
    "search_code_desc": {"zh": "在目录中按文本模式搜索匹配内容。", "en": "Search for matching text within a directory."},
    "edit_file_desc": {"zh": "在文件中替换指定文本。", "en": "Replace a specific string in a file."},
    "execute_command_desc": {
        "zh": "执行 shell 命令。默认在后台脱离终端执行并返回 PID 与日志路径，避免阻塞；对 ls、pytest、build 等短时命令可设置 background=false 同步等待并拿到精确退出码；对 npm run dev 等常驻服务保持后台执行，可通过 health_check_url 轮询就绪状态。",
        "en": "Execute a shell command. By default the command runs detached in the background and returns a PID plus a log path so the agent is never blocked; for short-lived commands such as 'ls', 'pytest' or 'npm run build' set background=false to wait synchronously and get the exact exit code; for persistent services such as 'npm run dev' keep background execution and optionally provide health_check_url to poll for readiness.",
    },
    "execute_command_command_desc": {"zh": "要执行的 shell 命令。", "en": "The shell command to execute."},
    "execute_command_background_desc": {
        "zh": "true 表示后台常驻执行（默认）；false 表示同步等待命令结束后返回退出码。",
        "en": "true runs the command detached in the background (default); false waits synchronously until the command exits and returns its exit code.",
    },
    "execute_command_cwd_desc": {"zh": "命令执行的工作目录。", "en": "Working directory to execute the command in."},
    "execute_command_timeout_desc": {"zh": "同步执行（background=false）时的超时秒数，默认 120。", "en": "Timeout in seconds for synchronous execution (background=false). Defaults to 120."},
    "execute_command_health_check_desc": {"zh": "仅 background=true 时有效：轮询该 URL 判断服务是否就绪。", "en": "Only used when background=true: URL polled to determine whether the service is ready."},
    "execute_command_log_path_desc": {"zh": "仅 background=true 时有效：stdout/stderr 写入的日志文件路径，缺省自动生成。", "en": "Only used when background=true: log file path for stdout/stderr; auto-generated when omitted."},
    "memory_search_desc": {"zh": "在长期记忆库中检索与查询相关的历史记忆。", "en": "Search the long-term memory store for memories relevant to the query."},
    "memory_search_query_desc": {"zh": "检索关键词或问题。", "en": "Keywords or question to search for."},
    "memory_search_limit_desc": {"zh": "最多返回的记忆条数，默认取配置值。", "en": "Maximum number of memories to return; defaults to the configured value."},
    "memory_add_desc": {"zh": "向长期记忆库写入一条简洁、自包含、可复用的记忆（事实、偏好、决策或经验）。", "en": "Write one concise, self-contained, reusable memory (fact, preference, decision or lesson) into the long-term memory store."},
    "memory_add_content_desc": {"zh": "记忆内容，单条不超过 300 字，禁止包含密钥、密码等敏感信息。", "en": "Memory content, at most 300 characters per entry; must not contain secrets such as API keys or passwords."},
    "memory_add_kind_desc": {"zh": "记忆类型：fact（事实）/ preference（偏好）/ decision（决策）/ lesson（经验），默认 fact。", "en": "Memory kind: fact / preference / decision / lesson; defaults to fact."},
    "memory_list_desc": {"zh": "列出最近写入的长期记忆条目。", "en": "List the most recently written long-term memory entries."},
    "memory_list_limit_desc": {"zh": "最多列出的条数，默认 10。", "en": "Maximum number of entries to list; defaults to 10."},
    "memory_delete_desc": {"zh": "按 ID 删除一条长期记忆。", "en": "Delete a long-term memory entry by ID."},
    "memory_delete_id_desc": {"zh": "要删除的记忆 ID。", "en": "ID of the memory entry to delete."},
    "path_desc": {"zh": "要读取的文件路径。", "en": "Path of the file to read."},
    "write_path_desc": {"zh": "要写入的目标文件路径。", "en": "Target file path to write to."},
    "content_desc": {"zh": "要写入的文件内容。", "en": "Content to write to the file."},
    "list_dir_path_desc": {"zh": "要列出的目录路径。", "en": "Directory path to list."},
    "list_dir_recursive_desc": {"zh": "是否递归遍历子目录。", "en": "Whether to recursively traverse subdirectories."},
    "interrupt": {"zh": "用户中断", "en": "Interrupted by user"},
    "not_detected": {"zh": "未检测到", "en": "Not detected"},
    "device_environment": {"zh": "设备工程环境：", "en": "Device environment:"},
    "device_time": {"zh": "当前设备时间：", "en": "Current device time:"},
    "device_os": {"zh": "当前操作系统信息：", "en": "Current operating system info:"},
    "device_software": {"zh": "当前设备软件环境信息：", "en": "Current software environment info:"},
    "device_workdir": {"zh": "当前工作目录：", "en": "Current working directory:"},
    "invalid_tool_args": {"zh": "工具参数不是有效 JSON，原始内容：%s", "en": "Tool arguments are not valid JSON. Raw content: %s"},
    "config_header": {"zh": "配置字段说明：", "en": "Configuration field descriptions:"},
    "config_file_read_error": {"zh": "配置文件读取失败，请检查 {path}: {error}", "en": "Failed to read config file. Please check {path}: {error}"},
    "config_file_format_error": {"zh": "配置文件格式不正确，期望 JSON 对象: {path}", "en": "Config file format is invalid. Expected a JSON object: {path}"},
    "config_incomplete": {"zh": "模型配置不完整，首次使用需正确配置模型才能够使用。\n请在 {path} 中完善以下字段：{fields}", "en": "The model configuration is incomplete. Please complete the required fields in {path} before using the agent.\nMissing fields: {fields}"},
    "config_api_key_error": {"zh": "未配置 API Key，请先在 config/model_config.json 中填写 api_key。", "en": "API key is not configured. Please fill in the api_key field in the config file first."},
    "mcp_discover_failed": {"zh": "发现 MCP 工具失败", "en": "Failed to discover MCP tools"},
    "mcp_tool_not_found": {"zh": "未找到 MCP 工具对应的服务配置: {name}", "en": "No MCP server configuration found for tool: {name}"},
    "mcp_tool_unavailable": {"zh": "MCP 工具不可用：{name}，已忽略。", "en": "MCP tool unavailable: {name}; it will be ignored."},
    "mcp_tool_failed": {"zh": "执行 MCP 工具失败", "en": "Failed to execute MCP tool"},
    "tool_missing_arg": {"zh": "{name} 缺少 {arg} 参数。", "en": "{name} is missing the {arg} argument."},
    "unknown_tool": {"zh": "未知工具: {name}", "en": "Unknown tool: {name}"},
    "start_model_call": {"zh": "开始调用模型", "en": "Starting model call"},
    "model_request_params": {"zh": "调试-模型请求参数", "en": "Debug - model request parameters"},
    "model_call_finished": {"zh": "调用模型结束", "en": "Model call finished"},
    "model_raw_response": {"zh": "调试-模型原始返回", "en": "Debug - raw model response"},
    "cancelled": {"zh": "已取消", "en": "Cancelled"},
    "model_cancelled": {"zh": "用户中断了当前模型调用。", "en": "The current model call was interrupted by the user."},
    "model_error": {"zh": "调用模型时出错", "en": "Error while calling the model"},
    "model_quota_error": {"zh": "模型调用失败：检测到配额/限流问题（可能是免费额度已耗尽或未开通付费）。\n请检查配置中的 api_key 与 base_url，或开通/充值服务后重试。", "en": "Model call failed: quota or rate-limit issue detected (the free quota may be exhausted or billing may not be enabled).\nPlease check the api_key and base_url in the config, or enable/top up the service and try again."},
    "model_502_error": {"zh": "模型调用失败：上游模型服务返回了 502（Bad Gateway）或服务端异常。\n这通常是模型服务端暂时不可用、代理转发异常或服务正在重启造成的。\n请稍后重试，并确认 base_url 指向的是可正常提供 /chat/completions 的服务。", "en": "Model call failed: the upstream model service returned 502 (Bad Gateway) or a server-side error.\nThis is usually caused by a temporary outage, proxy forwarding issue, or service restart.\nPlease try again later and confirm that base_url points to a service that serves /chat/completions correctly."},
    "model_call_failed": {"zh": "模型调用失败：{error}", "en": "Model call failed: {error}"},
    "tool_result_title": {"zh": "工具调用结果", "en": "Tool execution result"},
    "config_error_title": {"zh": "配置错误", "en": "Configuration error"},
    "quota_hint": {"zh": "免费额度", "en": "free quota"},
    "tool_execution": {"zh": "开始执行工具：{name}，参数：{args}", "en": "Starting tool execution: {name}, args: {args}"},
    "tool_subprocess_failed": {"zh": "子进程启动失败，cwd=%s，回退到 %s: %s", "en": "Failed to start subprocess in cwd=%s, falling back to %s: %s"},
    "welcome_message": {
        "zh": "输入:\n/exit 退出，\n/clear 清空本地会话，\n/task-start 开始任务上下文，\n/task-end 生成最终提示，\n/memory 查看长期记忆。\nCtrl+C 可中断当前执行。\n\n启动命令示例：\npython main.py -l zh\npython main.py -t \"你的任务\" -d ./workspace -l en\npython main.py --reset-session\n\n参数说明：\n-t / --task：一次性任务内容\n-d / --workdir：指定工作目录\n-l / --lang：选择语言（zh / en）\n-amc / --agent-messages-count：单次请求保留的消息数量(默认 8)\n-rd / --request-delay：请求间隔秒数(默认 1)\n-hc / --history-count：会话历史保留的消息数量(默认 6)\n--reset-session：重置本地会话记录。",
        "en": "Input:\n/exit to exit,\n/clear to clear the local session,\n/task-start to start a task context,\n/task-end to generate the final prompt,\n/memory to inspect long-term memory.\nCtrl+C can interrupt the current execution.\n\nStartup command examples:\npython main.py -l zh\npython main.py -t \"your task\" -d ./workspace -l en\npython main.py --reset-session\n\nOptions:\n-t / --task: one-off task content\n-d / --workdir: specify the working directory\n-l / --lang: choose language (zh / en)\n-amc / --agent-messages-count: number of messages to keep per model request (default: 8)\n-rd / --request-delay: delay in seconds between model requests (default: 1)\n-hc / --history-count: number of messages to persist in session history (default: 6)\n--reset-session: reset the local session history.",
    },
    "startup_title": {"zh": "启动", "en": "Start"},
    "exit_message": {"zh": "已退出。", "en": "Exited."},
    "bye_message": {"zh": "再见。", "en": "Goodbye."},
    "clear_session_message": {"zh": "会话记录与最近对话记录已清空。", "en": "Session history and recent conversation history have been cleared."},
    "workdir_message": {"zh": "工作目录: {path}", "en": "Working directory: {path}"},
    "cli_config_title": {"zh": "Cli 配置", "en": "CLI configuration"},
    "mcp_connected": {"zh": "已接入 MCP 工具：{count} 个", "en": "Connected MCP tools: {count}"},
    "mcp_config_title": {"zh": "MCP 配置", "en": "MCP configuration"},
    "session_reset_message": {"zh": "会话记录已重置。", "en": "Session history has been reset."},
    "debug_enabled_message": {"zh": "调试模式已开启，会输出模型请求参数与原始返回内容。", "en": "Debug mode is enabled; model request parameters and raw responses will be shown."},
    "debug_config_title": {"zh": "调试配置", "en": "Debug configuration"},
    "task_cancelled": {"zh": "已取消当前任务。", "en": "The current task has been cancelled."},
    "interrupted_title": {"zh": "已中断", "en": "Interrupted"},
    "runtime_error_title": {"zh": "运行时错误", "en": "Runtime error"},
    "tool_execution_error": {"zh": "工具执行错误", "en": "Tool execution error"},
    "task_end_error": {"zh": "Task End 错误", "en": "Task End error"},
    "task_end_notice": {"zh": "Task End 提示", "en": "Task End notice"},
    "task_end_file_missing": {"zh": "未找到对话记录文件：{path}", "en": "Conversation history file not found: {path}"},
    "task_end_empty": {"zh": "对话记录为空或格式不正确。", "en": "Conversation history is empty or malformed."},
    "task_end_no_task_start": {"zh": "未在 recent_conversations.md 中找到包含 /task-start 的轮次。", "en": "No round containing /task-start was found in recent_conversations.md."},
    "task_end_generate_failed": {"zh": "生成失败", "en": "Generation failed"},
    "task_end_no_prompt": {"zh": "模型未返回任何最终提示。", "en": "The model did not return a final prompt."},
    "task_end_result": {"zh": "生成结果", "en": "Generation result"},
    "task_end_completed": {"zh": "已生成最终提示并写入: {path}", "en": "Final prompt generated and written to: {path}"},
    "task_end_done": {"zh": "Task End 完成", "en": "Task End complete"},
    "task_end_constraints": {
        "zh": "你是提示词优化专家，负责将对话记录整理为更清晰、可执行的最终任务提示词。\n\n### 生成最终提示词时，请严格遵循以下约束：\n1. 只输出最终改进后的提示词文本，不要添加任何解释、元信息或注释。\n2. 保留用户原始目标、关键上下文、澄清或变更后的要求、待执行步骤与预期产物。\n3. 语言必须清晰、可执行、步骤化，适合直接给后续执行器使用。\n4. 如果上下文中存在不确定信息，应显式写成“待确认”或“需验证”的约束项。\n5. 输出长度控制在 18000 字符以内。",
        "en": "You are a prompt-refinement expert responsible for distilling the conversation record into a clearer, executable final task prompt.\n\n### When generating the final prompt, strictly follow these constraints:\n1. Output only the final refined prompt text; do not add explanations, metadata or comments.\n2. Preserve the user's original goal, key context, clarified or changed requirements, pending steps and expected deliverables.\n3. The language must be clear, executable and step-oriented, suitable for direct use by a downstream executor.\n4. If the context contains uncertain information, write it explicitly as a \"to be confirmed\" or \"needs verification\" constraint.\n5. Keep the output within 18000 characters.",
    },
    "task_end_user_prompt": {
        "zh": "分析对话记录帮我生成最终提示词,把最终提示词内容输出给我。\n### 对话记录\n{compiled_text}",
        "en": "Analyze the conversation record and generate the final prompt for me. Output the final prompt content only.\n### Conversation record\n{compiled_text}",
    },
    "starting_tool_call": {"zh": "开始调用工具", "en": "Starting tool call"},
    "current_tool_cancelled": {"zh": "已取消当前工具执行。", "en": "The current tool execution has been cancelled."},
    "cli_parser_description": {"zh": "Prompt Copilot CLI 编程 Agent", "en": "Prompt Copilot CLI coding agent"},
    "task_argument_help": {"zh": "一次性任务内容，适合单次执行。", "en": "One-off task content, suitable for a single run."},
    "workdir_argument_help": {"zh": "工作目录。", "en": "Working directory."},
    "reset_session_help": {"zh": "重置本地持久化会话记录。", "en": "Reset local persisted session history."},
    "prompt_placeholder": {"zh": "你想让我做什么？> ", "en": "What do you want me to do? > "},
    "toolbar_help": {"zh": "可用命令: /exit /clear /task-start /task-end /memory  | Tab 补全 | Ctrl+C 可中断当前执行", "en": "Available commands: /exit /clear /task-start /task-end /memory | Tab completion | Ctrl+C can interrupt the current execution"},
    "token_usage_label": {"zh": "累计 token", "en": "Cumulative tokens"},
    "prompt_tokens_label": {"zh": "prompt", "en": "prompt"},
    "completion_tokens_label": {"zh": "completion", "en": "completion"},
    "total_tokens_label": {"zh": "total", "en": "total"},
    "token_usage_unavailable": {"zh": "无 token 使用信息", "en": "No token usage information"},
    "context_size_label": {"zh": "提交上下文大小", "en": "Submitted context size"},
    "response_length_label": {"zh": "回复内容长度", "en": "Response content length"},
    "recent_conversations_header": {"zh": "# 最近对话记录", "en": "# Recent conversations"},
    "task_end_generation_failed_log": {"zh": "调用模型生成最终提示失败", "en": "Failed to generate the final prompt with the model"},
    "final_prompt_header": {"zh": "# 最终提示词", "en": "# Final prompt"},
    "memory_title": {"zh": "长期记忆", "en": "Long-term memory"},
    "memory_disabled": {"zh": "长期记忆已在配置中禁用（memory.enabled=false）。", "en": "Long-term memory is disabled in the configuration (memory.enabled=false)."},
    "memory_empty": {"zh": "记忆库为空。", "en": "The memory store is empty."},
    "memory_added": {"zh": "已写入记忆 #{id}", "en": "Memory #{id} stored"},
    "memory_deleted": {"zh": "已删除记忆 #{id}", "en": "Memory #{id} deleted"},
    "memory_delete_not_found": {"zh": "未找到记忆 #{id}", "en": "Memory #{id} not found"},
    "memory_reject_secret": {"zh": "记忆内容疑似包含密钥/密码等敏感信息，已拒绝写入。", "en": "The memory content appears to contain secrets such as keys or passwords and was rejected."},
    "memory_reject_empty": {"zh": "记忆内容为空，已忽略。", "en": "The memory content is empty and was ignored."},
    "memory_reject_too_long": {"zh": "记忆内容超过 300 字，请精简后重试。", "en": "The memory content exceeds 300 characters; please shorten it and retry."},
    "memory_extract_failed": {"zh": "自动提取长期记忆失败", "en": "Failed to auto-extract long-term memories"},
    "memory_section_header": {"zh": "## 长期记忆（来自历史会话，供参考）", "en": "## Long-term memory (from previous sessions, for reference)"},
}


def t(key: str, **kwargs: Any) -> str:
    """Translate *key* into the active UI language, applying ``str.format`` kwargs."""
    mapping = TRANSLATIONS.get(key, {})
    text = mapping.get(UI_SYSTEM_LANGUAGE, mapping.get("en", key))
    if kwargs:
        return text.format(**kwargs)
    return text
