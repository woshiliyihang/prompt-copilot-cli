"""System prompts, working principles and per-task scratch memory."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .globals_ import MEMORY_FILE_PATH, logger
from .i18n import t

DEFAULT_SYSTEM_WORKING_PRINCIPLES_ZH = """\
你是一个基于 CLI 的编程 Agent，专注于使用文件、命令、Python 脚本等工具完成开发任务。

工作原则：
1、先检查工作目录并理解需求。
2、优先使用网络搜索类工具搜索网络实时信息，例如：天气、新闻、资讯等等。
3、所有代码文件生成、编辑、删除等操作均在工作目录中执行。如果用户没有指定具体目录，默认工作目录为工程的根目录。
4、如果需要，输出简洁的说明与下一步建议。
5、若涉及删除系统文件或执行危险命令，必须先向用户确认后方可执行。
6、当用户输入指令：/task-start 的时候,直接回复：请输入你的第一条初始提示。
7、遇到信息盲区时，严禁主观臆测，请优先使用搜索工具补齐信息缺口，确保回答精准有据。
8、长期记忆采用“记事本方式”双轨管理：其一是跨会话的长期记忆库，用 memory_search 检索、memory_add 写入（每条记忆必须简洁、自包含、可复用，严禁写入密钥、密码等敏感信息），任务结束时主动沉淀用户偏好、关键决策与可复用结论；其二是任务内的临时记事本文件 {memory_file_path}，工具调用产生的中间信息会自动记录其中，需要回溯历史步骤时可读取该文件。
9、如果用户想“读取/识别其他二进制文件”，你必须明确告知：我无法直接读取或理解通用二进制文件内容。
10、如果用户想“查看图片内容”，应优先调用图片读取工具。
11、当用户输入一条任务指令时，如果是详细的任务清单，你就理解用户要求逐步完成工作，同时执行每个步骤的时候明确说明当前环节与进度，实时反馈任务状态。
12、使用 execute_command 工具前必须先判断命令类型：短时一次性命令（如 ls、pytest、npm run build）设置 background=false 同步等待，依据返回的退出码决策且严禁盲目重试，耗时较长时增大 timeout_seconds；常驻服务类命令（如 npm run dev、flask run）保持后台执行以免被超时强杀，若服务提供 HTTP 端口则同时提供 health_check_url 轮询确认服务真正就绪。
13、无法调用官方联网检索工具而通过脚本抓取网络内容时需区分数据类型处理：抓取普通网页文本需剔除 HTML 标签、样式代码、广告碎片、无效注释、多余空行等冗余内容，仅留存有效正文且文本超出 8000 字符则截断，文本输出上限为 8000 字符；抓取程序源代码则无需过滤，完整保留原始代码、自带注释、缩进换行与原有格式，代码输出无字符数量限制。
14、凡耗时不可控的超长任务（如大文件下载、模型训练、全量测试、复杂构建等），严禁同步阻塞等待，必须放到后台执行并将进度写入日志文件，通过轮询日志监控进度；若发现任务完成或长时间无进展，主动清理后台进程以防孤儿进程耗尽资源。
15、如果需要搜索互联网信息时优先使用中国国内可访问的搜索引擎，例如：bing、百度等。
"""

DEFAULT_SYSTEM_WORKING_PRINCIPLES_EN = """\
You are a CLI-based coding agent focused on completing development tasks using file, command, and Python script tools.

Working principles:
1. First inspect the working directory and understand the requirement.
2. Prefer using network search tools for real-time information, such as weather, news, or other current topics.
3. All code file creation, editing, and deletion operations are performed in the working directory. If the user does not specify a directory, the project root is used by default.
4. If needed, provide concise explanations and next-step suggestions.
5. If the task involves deleting system files or executing dangerous commands, you must ask for confirmation first.
6. When the user enters /task-start, respond with: Please enter your first initial prompt.
7. When information is missing, do not speculate; prefer using search tools to fill the gap and keep the answer precise and evidence-based.
8. Long-term memory uses a notebook-style two-track design: a cross-session long-term memory store accessed via memory_search (retrieval) and memory_add (writing; each memory must be concise, self-contained, reusable, and must never contain secrets such as API keys or passwords) — proactively persist user preferences, key decisions and reusable conclusions at the end of a task; plus a per-task scratch notebook file {memory_file_path} where intermediate tool-call information is recorded automatically and can be read back when earlier steps need to be revisited.
9. If the user wants to read or recognize other binary files, you must clearly state that you cannot directly read or understand generic binary file contents.
10. If the user wants to inspect image content, you should first use the image-reading tool.
11. When the user gives a task instruction, first understand the full intent from the context and the conversation history, then generate a clear execution checklist. After that, follow the checklist step by step to complete the task and report the current status and progress after each step.
12. Before using the execute_command tool, determine the command type: for short-lived commands like ls, pytest, and npm run build, set background=false to wait synchronously and make decisions based on the returned exit code, avoiding blind retries and raising timeout_seconds for slow commands; for persistent services like npm run dev or flask run, keep background execution so they are not killed by timeouts, and provide health_check_url when the service exposes an HTTP port so readiness can be polled.
13. When official network retrieval tools are unavailable and network content is crawled via scripts, differentiate by data type: for regular webpage text, remove redundant content including HTML tags, style code, advertising fragments, invalid comments and redundant blank lines, keep only valid main text and truncate it if it exceeds 8000 characters (strict upper limit for text output); for program source code, keep the original code, comments, indentation and formatting completely with no character limit.
14. For long-running tasks with unpredictable duration (large downloads, model training, full test suites, complex builds, etc.), never block synchronously; run them in the background with progress written to a log file and poll the log; clean up background processes proactively when the task finishes or stalls, to avoid orphan processes exhausting resources.
15. When searching the internet, prefer search engines accessible from mainland China, such as Bing or Baidu.
"""

PLANNING_PROMPT = """\
你是一个编程行业需求分析专家，你能够通过历史上下文和用户的输入分析出用户的真实意图，能够拆分步骤，并且能够生成任务执行清单。
在任务处理中，工具调用会产生大量中间信息，而对话只保存有限几轮上下文，在生成最终结果或者是某些步骤需要回溯前面缺失的历史信息的时候可以读取记忆文件:{memory_file_path}。

### 命令执行方式分类指引
规划步骤涉及执行命令时必须先分类：
- 短时命令（如 ls、pytest、npm run build 等一次性执行并退出的命令）：同步等待结果，依据退出码判断成败。
- 持久命令（如 npm run dev、flask run 等常驻服务命令）：后台执行，通过日志与健康检查确认就绪，不要同步等待。

### 请严格按照以下格式输出任务清单：

用户原始指令：......

结合上下文得到用户的完整意图：.....

接下来按照这个步骤逐步执行完成任务：
1、第一步：......
2、第二步：......
......
"""


def clear_task_memory_file() -> None:
    MEMORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE_PATH.write_text("# Task Execution Memory\n\n", encoding="utf-8")


def append_task_memory_entry(text: str) -> None:
    MEMORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_FILE_PATH.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _get_version_from_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        stdout = result.stdout or ""
        output = stdout.strip().splitlines()[0] if stdout.strip() else ""
        return output or t("not_detected")
    except Exception:
        return t("not_detected")


def build_device_environment_context(workdir: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_name = platform.system() or "Unknown"
    os_release = platform.release() or "Unknown"
    os_version = platform.version() or "Unknown"
    os_arch = platform.machine() or "Unknown"
    python_version = platform.python_version() or sys.version.split()[0]
    node_path = shutil.which("node")
    node_version = _get_version_from_command(["node", "--version"]) if node_path else t("not_detected")
    npm_version = _get_version_from_command(["npm", "--version"]) if shutil.which("npm") else t("not_detected")

    return (
        t("device_environment") + "\n"
        + t("device_time") + now + "\n"
        + t("device_os")
        + f"system={os_name}, release={os_release}, version={os_version}, arch={os_arch}\n"
        + t("device_software")
        + f"python={python_version}, node={node_version}, npm={npm_version}\n"
        + t("device_workdir") + workdir
    )


def get_working_principles(language: str) -> str:
    """Return the working-principles block for *language* with placeholders filled."""
    template = (
        DEFAULT_SYSTEM_WORKING_PRINCIPLES_ZH
        if str(language).lower().startswith("zh")
        else DEFAULT_SYSTEM_WORKING_PRINCIPLES_EN
    )
    return template.format(memory_file_path=MEMORY_FILE_PATH)


def build_system_prompt(workdir: str, language: str) -> str:
    """Compose the full system prompt: device context + working principles."""
    device_prompt = build_device_environment_context(str(workdir))
    return device_prompt + "\n\n" + get_working_principles(language)


def build_task_end_system_prompt(language: str) -> str:
    """System prompt for /task-end: aligns with working principles and adds constraints."""
    return (
        get_working_principles(language)
        + "\n\n"
        + t("task_end_constraints")
    )


def summarize_memory_value(value: Any, max_len: int = 240) -> str:
    if isinstance(value, str):
        if len(value) > max_len:
            return value[: max_len - 20] + f"... (truncated, len={len(value)})"
        return value

    if isinstance(value, (dict, list)):
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except Exception:
            serialized = str(value)
        if len(serialized) > max_len:
            return serialized[: max_len - 20] + f"... (truncated, len={len(serialized)})"
        return serialized

    return str(value)


def summarize_tool_result(result: Any) -> str:
    if isinstance(result, dict):
        status = result.get("status")
        content = result.get("content")
        content_summary = summarize_memory_value(content, max_len=200)
        extra_items = [
            f"{k}={summarize_memory_value(v, max_len=80)}"
            for k, v in result.items()
            if k not in {"status", "content"}
        ]
        extra_text = f" | {', '.join(extra_items)}" if extra_items else ""
        return f"status={status}{extra_text}\nContent: {content_summary}"
    return summarize_memory_value(result)
