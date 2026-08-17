"""Tool definitions and execution: file ops, commands, Python scripts, memory."""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import globals_
from . import memory as memory_module
from .globals_ import ROOT, TOOL_SUBPROCESS_TIMEOUT, ensure_not_interrupted, logger, settings
from .i18n import t
from .memory import MAX_MEMORY_CHARS
from .ui import console

# --------------------------------------------------------------------------
# Built-in tool definitions
# --------------------------------------------------------------------------


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _path_prop(desc_key: str = "path_desc") -> dict[str, Any]:
    return {"type": "string", "description": t(desc_key)}


def build_tool_definitions(memory_enabled: bool = True) -> list[dict[str, Any]]:
    definitions = [
        _tool("read_file", t("read_file_desc"), {"path": _path_prop()}, ["path"]),
        _tool(
            "write_file",
            t("write_file_desc"),
            {"path": _path_prop("write_path_desc"), "content": {"type": "string", "description": t("content_desc")}},
            ["path", "content"],
        ),
        _tool("delete_file", t("delete_file_desc"), {"path": _path_prop()}, ["path"]),
        _tool("create_directory", t("create_directory_desc"), {"path": _path_prop()}, ["path"]),
        _tool("delete_directory", t("delete_directory_desc"), {"path": _path_prop()}, ["path"]),
        _tool(
            "rename_path",
            t("rename_path_desc"),
            {"old_path": _path_prop(), "new_path": _path_prop("write_path_desc")},
            ["old_path", "new_path"],
        ),
        _tool(
            "copy_file",
            t("copy_file_desc"),
            {"source_path": _path_prop(), "destination_path": _path_prop("write_path_desc")},
            ["source_path", "destination_path"],
        ),
        _tool("read_image_as_base64", t("read_image_as_base64_desc"), {"path": _path_prop()}, ["path"]),
        _tool(
            "list_dir",
            t("list_dir_desc"),
            {
                "path": _path_prop("list_dir_path_desc"),
                "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")},
            },
            ["path"],
        ),
        _tool(
            "search_code",
            t("search_code_desc"),
            {
                "path": _path_prop("list_dir_path_desc"),
                "pattern": {"type": "string", "description": "Text pattern to search for."},
                "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")},
                "max_results": {"type": "integer", "description": "Maximum number of matches to return."},
            },
            ["path", "pattern"],
        ),
        _tool(
            "edit_file",
            t("edit_file_desc"),
            {
                "path": _path_prop("write_path_desc"),
                "old_string": {"type": "string", "description": "The text to replace."},
                "new_string": {"type": "string", "description": "The replacement text."},
            },
            ["path", "old_string", "new_string"],
        ),
        _tool(
            "execute_command",
            t("execute_command_desc"),
            {
                "command": {"type": "string", "description": t("execute_command_command_desc")},
                "background": {"type": "boolean", "description": t("execute_command_background_desc")},
                "cwd": {"type": "string", "description": t("execute_command_cwd_desc")},
                "timeout_seconds": {"type": "integer", "description": t("execute_command_timeout_desc")},
                "health_check_url": {"type": "string", "description": t("execute_command_health_check_desc")},
                "output_log_path": {"type": "string", "description": t("execute_command_log_path_desc")},
            },
            ["command"],
        ),
        _tool(
            "execute_python_script",
            "Execute a Python script or a block of Python code. Use the 'timeout_seconds' parameter to control the maximum execution time and prevent the process from hanging indefinitely.",
            {
                "script": {"type": "string", "description": "The Python script content to execute."},
                "cwd": {"type": "string", "description": "The working directory for the script execution."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds before the process is terminated. Defaults to 120. Set a shorter timeout for quick commands to avoid waiting.",
                },
            },
            ["script", "cwd"],
        ),
    ]

    if memory_enabled:
        definitions.extend(
            [
                _tool(
                    "memory_search",
                    t("memory_search_desc"),
                    {
                        "query": {"type": "string", "description": t("memory_search_query_desc")},
                        "limit": {"type": "integer", "description": t("memory_search_limit_desc")},
                    },
                    ["query"],
                ),
                _tool(
                    "memory_add",
                    t("memory_add_desc"),
                    {
                        "content": {"type": "string", "description": t("memory_add_content_desc")},
                        "kind": {"type": "string", "description": t("memory_add_kind_desc")},
                    },
                    ["content"],
                ),
                _tool(
                    "memory_list",
                    t("memory_list_desc"),
                    {"limit": {"type": "integer", "description": t("memory_list_limit_desc")}},
                    [],
                ),
                _tool(
                    "memory_delete",
                    t("memory_delete_desc"),
                    {"id": {"type": "integer", "description": t("memory_delete_id_desc")}},
                    ["id"],
                ),
            ]
        )
    return definitions


TOOL_DEFINITIONS: list[dict[str, Any]] = build_tool_definitions(memory_enabled=True)

ACTIVE_MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_CONFIG: dict[str, Any] = {}
ACTIVE_MCP_TOOL_CONFIGS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_SERVER_BY_NAME: dict[str, dict[str, Any]] = {}


def refresh_tool_definitions(memory_enabled: bool = True) -> None:
    """Rebuild :data:`TOOL_DEFINITIONS` after settings (language/memory) change."""
    global TOOL_DEFINITIONS
    TOOL_DEFINITIONS = build_tool_definitions(memory_enabled=memory_enabled)


def get_tool_description(tool_call: Any) -> str:
    description = getattr(getattr(tool_call, "function", None), "description", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    tool_name = getattr(getattr(tool_call, "function", None), "name", None)
    if not isinstance(tool_name, str) or not tool_name:
        return ""

    for definition in TOOL_DEFINITIONS + ACTIVE_MCP_TOOL_DEFINITIONS:
        function = definition.get("function") or {}
        if function.get("name") == tool_name:
            return str(function.get("description", "")).strip()

    return ""


def safe_parse_tool_args(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning(t("invalid_tool_args"), raw_arguments)
            return {}
    return {}


def resolve_execution_cwd(cwd: Any, fallback: str | os.PathLike[str] | None = None) -> str:
    fallback_path = Path(fallback or Path.cwd()).expanduser()
    if cwd in (None, "", "."):
        return str(fallback_path)

    candidate = Path(str(cwd)).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve(strict=False)
    except Exception:
        return str(fallback_path)

    if resolved.exists() and resolved.is_dir():
        return str(resolved)

    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)
    except Exception:
        return str(fallback_path)


# --------------------------------------------------------------------------
# Command execution helpers
# --------------------------------------------------------------------------


def looks_like_background_service_command(command: Any) -> bool:
    """Heuristically detect long-running service commands (dev servers etc.)."""
    if isinstance(command, (list, tuple)):
        text = " ".join(str(part) for part in command)
    else:
        text = str(command or "")

    normalized = text.lower().strip()
    if not normalized:
        return False

    background_markers = (
        "npm run dev",
        "npm start",
        "pnpm dev",
        "pnpm start",
        "yarn dev",
        "yarn start",
        "vite",
        "next dev",
        "next start",
        "uvicorn",
        "flask run",
        "gunicorn",
        "python app.py",
        "python main.py",
        "python manage.py runserver",
        "python -m http.server",
        "http.server",
        "serve",
        "watch",
    )

    if any(marker in normalized for marker in background_markers):
        return True

    if normalized.startswith(("python ", "py ")) and any(script in normalized for script in ("app.py", "main.py", "manage.py")):
        return True

    return False


def _read_text_file_tail(path: str | os.PathLike[str], max_chars: int = 4000) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception:
        return ""


def _kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Best-effort termination of *process* and its children, cross-platform."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        pass
    finally:
        try:
            process.kill()
        except Exception:
            pass


def stream_background_process_output(process: subprocess.Popen[Any], log_path: str | os.PathLike[str]) -> None:
    """Mirror newly appended log content to the console while the process runs."""
    from rich.panel import Panel

    log_file = Path(log_path)

    def _watch_output() -> None:
        last_position = 0
        try:
            if log_file.exists():
                last_position = log_file.stat().st_size
        except Exception:
            last_position = 0

        while process.poll() is None:
            try:
                if log_file.exists():
                    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(last_position)
                        chunk = handle.read()
                        if chunk:
                            last_position = handle.tell()
                            if chunk.strip():
                                console.print(chunk.rstrip(), style="dim")
            except Exception:
                pass
            time.sleep(0.2)

        try:
            if log_file.exists():
                with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(last_position)
                    chunk = handle.read()
                    if chunk and chunk.strip():
                        console.print(chunk.rstrip(), style="dim")
        except Exception:
            pass

    threading.Thread(target=_watch_output, daemon=True).start()


def start_background_process(
    command: Any,
    cwd: str,
    timeout_seconds: int | None = None,
    output_log_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Start *command* detached from the terminal; output streams into a log file."""
    from rich.panel import Panel

    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    log_path = (
        Path(output_log_path).expanduser()
        if output_log_path
        else ROOT / "logs" / "background" / f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    startup_kwargs: dict[str, Any] = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "stdin": subprocess.DEVNULL,
    }

    if os.name == "nt":
        startup_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        if isinstance(command, (list, tuple)):
            process = subprocess.Popen(list(command), start_new_session=True, **startup_kwargs)
        else:
            process = subprocess.Popen(str(command), shell=True, start_new_session=True, **startup_kwargs)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning(t("tool_subprocess_failed"), cwd, safe_cwd, exc)
        return {"status": "error", "content": str(exc)}

    def _drain_output() -> None:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                while True:
                    chunk = process.stdout.readline() if process.stdout is not None else ""
                    if chunk == "":
                        break
                    handle.write(chunk)
                    handle.flush()
        except Exception:
            pass

    threading.Thread(target=_drain_output, daemon=True).start()

    if timeout_seconds is not None and timeout_seconds > 0:
        def _watch_timeout() -> None:
            try:
                time.sleep(timeout_seconds)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"\n[timeout] process terminated after {timeout_seconds}s\n")
                        handle.flush()
            except Exception:
                pass

        threading.Thread(target=_watch_timeout, daemon=True).start()

    console.print(Panel.fit(f"Streaming background output to {log_path}", title="Background process"))
    stream_background_process_output(process, log_path)

    return {
        "status": "ok",
        "content": f"Started background process (pid={process.pid})",
        "pid": process.pid,
        "cwd": safe_cwd,
        "log_path": str(log_path),
        "state": "running",
    }


def wait_for_health_check(url: str, timeout_seconds: int = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if getattr(response, "status", 0) < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_subprocess_command(command: Any, cwd: str, shell: bool = False, timeout: int | None = None) -> tuple[int, str, str]:
    """Run *command* synchronously with tree-safe timeout handling."""
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())

    popen_kwargs: dict[str, Any] = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(command, shell=shell, **popen_kwargs)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning(t("tool_subprocess_failed"), cwd, safe_cwd, exc)
        proc = subprocess.Popen(command, cwd=str(Path.cwd()), shell=shell, **popen_kwargs)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        return -1, "", f"Command timed out after {timeout} seconds"
    except KeyboardInterrupt:
        _kill_process_tree(proc)
        raise

    return proc.returncode, stdout or "", stderr or ""


def handle_execute_command(args: dict[str, Any], background_starter: Any | None = None) -> dict[str, Any]:
    """Dispatch execute_command: background by default, synchronous on demand.

    ``background_starter`` lets callers (notably the ``main`` module, whose
    attribute is monkeypatched in tests) inject an alternative starter.
    """
    command = args.get("command")
    if not command:
        return {"status": "error", "content": "Missing 'command' argument."}

    cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())

    # Default to background execution so the agent loop is never blocked by
    # runaway commands; short-lived commands can opt into synchronous mode.
    is_background = args.get("background")
    if is_background is None:
        is_background = True

    starter = background_starter or start_background_process

    if is_background:
        output_log_path = args.get("output_log_path") or args.get("log_path")
        if not output_log_path:
            output_log_path = (
                ROOT / "logs" / "background"
                / f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
            )

        result = starter(command, cwd, timeout_seconds=None, output_log_path=output_log_path)

        if result.get("status") == "ok":
            health_check_url = args.get("health_check_url")
            if health_check_url:
                try:
                    ready = wait_for_health_check(
                        str(health_check_url),
                        timeout_seconds=int(args.get("health_check_timeout", 20)),
                    )
                    result["health_check_ready"] = ready
                    result["content"] = (
                        f"Started background process (pid={result['pid']})"
                        + (" and health check succeeded." if ready else " but health check did not succeed yet.")
                    )
                except Exception:
                    result["health_check_ready"] = False
                    result["content"] = f"Started background process (pid={result['pid']})"

            time.sleep(0.5)  # give the drain thread a moment to write output
            result["output_tail"] = _read_text_file_tail(output_log_path, max_chars=4000)
            return result
        return result

    # Synchronous path: sentinel-based exit-code capture with a watchdog so a
    # silent, non-exiting command cannot hang the agent forever.
    try:
        timeout_seconds = int(args.get("timeout_seconds") or args.get("timeout") or 120)
    except (TypeError, ValueError):
        timeout_seconds = 120
    timeout_seconds = min(max(timeout_seconds, 5), TOOL_SUBPROCESS_TIMEOUT)

    sentinel = f"@@CMD_DONE_{uuid.uuid4().hex}@@"
    if platform.system() == "Windows":
        full_cmd = f"{command}\necho {sentinel} %errorlevel%\n"
    else:
        full_cmd = f"{command}\necho {sentinel} $?\n"

    popen_kwargs: dict[str, Any] = {
        "shell": True,
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }

    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(full_cmd, **popen_kwargs)

    timed_out = {"value": False}

    def _watchdog() -> None:
        time.sleep(timeout_seconds)
        if process.poll() is None:
            timed_out["value"] = True
            _kill_process_tree(process)

    watchdog = threading.Thread(target=_watchdog, daemon=True)
    watchdog.start()

    output_lines: list[str] = []
    exit_code = -1
    try:
        while True:
            line = process.stdout.readline()
            if not line:
                break
            if sentinel in line:
                try:
                    exit_code = int(line.split(sentinel)[-1].strip())
                except Exception:
                    exit_code = -1
                break
            output_lines.append(line)
            if len(output_lines) > 5000:
                output_lines.pop(0)
    except KeyboardInterrupt:
        _kill_process_tree(process)
        raise
    finally:
        if process.poll() is None:
            _kill_process_tree(process)

    content = "".join(output_lines)[-4000:]

    if timed_out["value"]:
        return {
            "status": "timeout",
            "exit_code": None,
            "content": (
                f"{content}\n[ERROR] Command timed out after {timeout_seconds} seconds and was forcefully terminated.\n"
                "[Hint] If this is a dev server, please use background=true."
            ),
        }
    return {
        "status": "success" if exit_code == 0 else "error",
        "exit_code": exit_code,
        "content": content,
    }


# --------------------------------------------------------------------------
# Memory tool handlers
# --------------------------------------------------------------------------


def handle_memory_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not query:
        return {"status": "error", "content": t("tool_missing_arg", name="memory_search", arg="query")}
    try:
        limit = int(args.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3
    results = memory_module.default_store().search(str(query), limit=limit)
    return {"status": "ok", "content": json.dumps(results, ensure_ascii=False)}


def handle_memory_add(args: dict[str, Any]) -> dict[str, Any]:
    content = args.get("content")
    if not content:
        return {"status": "error", "content": t("tool_missing_arg", name="memory_add", arg="content")}
    kind = str(args.get("kind") or "fact")
    outcome = memory_module.default_store().add(str(content), kind=kind)
    if outcome["status"] != "ok":
        code_map = {
            "empty": t("memory_reject_empty"),
            "too_long": t("memory_reject_too_long"),
            "secret": t("memory_reject_secret"),
        }
        return {"status": "error", "content": code_map.get(outcome["code"], outcome["content"])}
    if outcome.get("duplicate"):
        return {"status": "ok", "content": f"Duplicate of memory #{outcome['id']}; nothing written.", "id": outcome["id"]}
    return {"status": "ok", "content": t("memory_added", id=outcome["id"]), "id": outcome["id"]}


def handle_memory_list(args: dict[str, Any]) -> dict[str, Any]:
    try:
        limit = int(args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    rows = memory_module.default_store().list_recent(limit=limit)
    return {"status": "ok", "content": json.dumps(rows, ensure_ascii=False)}


def handle_memory_delete(args: dict[str, Any]) -> dict[str, Any]:
    memory_id = args.get("id")
    if memory_id is None:
        return {"status": "error", "content": t("tool_missing_arg", name="memory_delete", arg="id")}
    try:
        memory_id = int(memory_id)
    except (TypeError, ValueError):
        return {"status": "error", "content": t("tool_missing_arg", name="memory_delete", arg="id")}
    if memory_module.default_store().delete(memory_id):
        return {"status": "ok", "content": t("memory_deleted", id=memory_id)}
    return {"status": "error", "content": t("memory_delete_not_found", id=memory_id)}


# --------------------------------------------------------------------------
# Individual built-in tool implementations
# --------------------------------------------------------------------------


def _resolve_path_arg(args: dict[str, Any]) -> Any:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return None
    path = Path(str(file_path)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _tool_read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="read_file", arg="path")}
        logger.error("read_file missing path argument, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("read_file failed due to non-UTF8 content: %s", path)
        return {
            "status": "error",
            "content": f"Unable to read as UTF-8 text: {path}\nThis file may be binary or encoded in another charset.",
        }
    except Exception as exc:
        logger.exception("read_file failed: %s", path)
        return {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
    return {"status": "ok", "content": content}


def _tool_write_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="write_file", arg="path")}
        logger.error("write_file missing path argument, raw args: %s", args)
        return result
    content = args.get("content", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"status": "ok", "content": f"Written: {path}"}


def _tool_delete_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="delete_file", arg="path")}
        logger.error("delete_file missing path argument, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    path.unlink()
    return {"status": "ok", "content": f"Deleted: {path}"}


def _tool_create_directory(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="create_directory", arg="path")}
        logger.error("create_directory missing path argument, raw args: %s", args)
        return result
    path.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "content": f"Created directory: {path}"}


def _tool_delete_directory(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="delete_directory", arg="path")}
        logger.error("delete_directory missing path argument, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "error", "content": f"Directory does not exist: {path}"}
    shutil.rmtree(path)
    return {"status": "ok", "content": f"Deleted directory: {path}"}


def _tool_rename_path(args: dict[str, Any]) -> dict[str, Any]:
    old_path = args.get("old_path")
    new_path = args.get("new_path")
    if not old_path or not new_path:
        result = {"status": "error", "content": t("tool_missing_arg", name="rename_path", arg="old_path/new_path")}
        logger.error("rename_path missing arguments, raw args: %s", args)
        return result
    old = Path(str(old_path)).expanduser()
    new = Path(str(new_path)).expanduser()
    if not old.is_absolute():
        old = Path.cwd() / old
    if not new.is_absolute():
        new = Path.cwd() / new
    if not old.exists():
        return {"status": "error", "content": f"Path does not exist: {old}"}
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    return {"status": "ok", "content": f"Renamed: {old} -> {new}"}


def _tool_copy_file(args: dict[str, Any]) -> dict[str, Any]:
    source_path = args.get("source_path")
    destination_path = args.get("destination_path")
    if not source_path or not destination_path:
        result = {"status": "error", "content": t("tool_missing_arg", name="copy_file", arg="source_path/destination_path")}
        logger.error("copy_file missing arguments, raw args: %s", args)
        return result
    source = Path(str(source_path)).expanduser()
    destination = Path(str(destination_path)).expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if not source.exists():
        return {"status": "error", "content": f"Source file does not exist: {source}"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"status": "ok", "content": f"Copied: {source} -> {destination}"}


def _tool_read_image(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="read_image_as_base64", arg="path")}
        logger.error("read_image_as_base64 missing path argument, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "error", "content": f"Image file does not exist: {path}"}
    return {
        "status": "ok",
        "content": json.dumps(
            {"path": str(path), "message": "Image loaded into context for multimodal analysis."},
            ensure_ascii=False,
        ),
    }


def _tool_list_dir(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    if path is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="list_dir", arg="path")}
        logger.error("list_dir missing path argument, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "ok", "content": json.dumps([], ensure_ascii=False)}

    recursive = bool(args.get("recursive", False))
    if recursive:
        items = []
        for item in sorted(path.rglob("*")):
            try:
                items.append(str(item.resolve()))
            except OSError:
                items.append(str(item))
    else:
        items = sorted(str(p) for p in path.iterdir())
    return {"status": "ok", "content": json.dumps(items, ensure_ascii=False)}


def _tool_search_code(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    pattern = args.get("pattern")
    if path is None or not pattern:
        result = {"status": "error", "content": t("tool_missing_arg", name="search_code", arg="path/pattern")}
        logger.error("search_code missing arguments, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "ok", "content": json.dumps([], ensure_ascii=False)}

    recursive = bool(args.get("recursive", True))
    max_results = args.get("max_results")
    try:
        max_results = int(max_results) if max_results is not None else 50
    except (TypeError, ValueError):
        max_results = 50

    matches: list[dict[str, Any]] = []
    if path.is_file():
        candidates = [path]
    elif recursive:
        candidates = [item for item in sorted(path.rglob("*")) if item.is_file()]
    else:
        candidates = [item for item in sorted(path.iterdir()) if item.is_file()]

    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if str(pattern) in line:
                matches.append({"path": str(candidate.resolve()), "line": line_number, "content": line})
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return {"status": "ok", "content": json.dumps(matches, ensure_ascii=False)}


def _tool_edit_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path_arg(args)
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if path is None or old_string is None or new_string is None:
        result = {"status": "error", "content": t("tool_missing_arg", name="edit_file", arg="path/old_string/new_string")}
        logger.error("edit_file missing arguments, raw args: %s", args)
        return result
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    try:
        original_text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("edit_file failed: %s", path)
        return {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}

    if old_string not in original_text:
        return {"status": "error", "content": f"Target text not found in file: {path}"}

    updated_text = original_text.replace(old_string, new_string, 1)
    path.write_text(updated_text, encoding="utf-8")
    return {"status": "ok", "content": f"Updated: {path}"}


def _tool_execute_python_script(args: dict[str, Any]) -> dict[str, Any]:
    script = args.get("script")
    if not script:
        result = {"status": "error", "content": t("tool_missing_arg", name="execute_python_script", arg="script")}
        logger.error("execute_python_script missing script argument, raw args: %s", args)
        return result
    cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())

    try:
        timeout_seconds = int(args.get("timeout_seconds") or 120)
    except (TypeError, ValueError):
        timeout_seconds = 120
    timeout_seconds = min(max(timeout_seconds, 5), TOOL_SUBPROCESS_TIMEOUT)

    # Use a unique temp script so parallel invocations never clobber each other
    # and always clean up afterwards.
    fd, script_path = tempfile.mkstemp(prefix="copilot_script_", suffix=".py", dir=cwd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(script)
        returncode, stdout, stderr = run_subprocess_command(
            [sys.executable, script_path],
            cwd,
            shell=False,
            timeout=timeout_seconds,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    response = {
        "status": "ok" if returncode == 0 else "error",
        "content": stdout + stderr,
        "returncode": returncode,
    }
    logger.info("execute_python_script result: %s", response)
    return response


_BUILTIN_HANDLERS: dict[str, Any] = {
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "delete_file": _tool_delete_file,
    "create_directory": _tool_create_directory,
    "delete_directory": _tool_delete_directory,
    "rename_path": _tool_rename_path,
    "copy_file": _tool_copy_file,
    "read_image_as_base64": _tool_read_image,
    "list_dir": _tool_list_dir,
    "search_code": _tool_search_code,
    "edit_file": _tool_edit_file,
    "execute_python_script": _tool_execute_python_script,
    "memory_search": handle_memory_search,
    "memory_add": handle_memory_add,
    "memory_list": handle_memory_list,
    "memory_delete": handle_memory_delete,
}


def execute_tool_call(tool_call: Any, background_starter: Any | None = None) -> dict[str, Any]:
    """Execute a single tool call and return its result dict.

    ``background_starter`` is forwarded to :func:`handle_execute_command` so
    callers can substitute the background-process implementation (tests do).
    """
    from .mcp import run_mcp_tool  # deferred to avoid circular import

    name = getattr(tool_call.function, "name", "")
    args = safe_parse_tool_args(getattr(tool_call.function, "arguments", {}))
    ensure_not_interrupted()
    logger.info(t("tool_execution", name=name, args=args))

    if name == "execute_command":
        result = handle_execute_command(args, background_starter=background_starter)
        logger.info("execute_command result: %s", result)
        return result

    handler = _BUILTIN_HANDLERS.get(name)
    if handler is not None:
        result = handler(args)
        logger.info("%s result: %s", name, result)
        return result

    if name in {item["function"]["name"] for item in ACTIVE_MCP_TOOL_DEFINITIONS}:
        logger.info("Executing MCP tool: %s, args: %s", name, args)
        return run_mcp_tool(name, args)

    result = {"status": "error", "content": t("unknown_tool", name=name)}
    logger.error("Unknown tool call: %s", name)
    return result
