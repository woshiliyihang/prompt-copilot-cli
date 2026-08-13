from __future__ import annotations
from config import INTERRUPTION_REQUESTED
from config import TOOL_SUBPROCESS_TIMEOUT
from config import t
from mcp import ACTIVE_MCP_TOOL_DEFINITIONS
from mcp import run_mcp_tool
from pathlib import Path
from process import resolve_execution_cwd
from process import run_subprocess_command
from types import SimpleNamespace
from typing import Any
from typing import List, Union
import base64
import json
import logging
import os
import shutil
import sys

logger = logging.getLogger("cli_agent")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": t("read_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": t("write_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("write_path_desc")},
                    "content": {"type": "string", "description": t("content_desc")},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": t("delete_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": t("create_directory_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": t("delete_directory_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_path",
            "description": t("rename_path_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": t("path_desc")},
                    "new_path": {"type": "string", "description": t("write_path_desc")}
                },
                "required": ["old_path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": t("copy_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": t("path_desc")},
                    "destination_path": {"type": "string", "description": t("write_path_desc")}
                },
                "required": ["source_path", "destination_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_image_as_base64",
            "description": t("read_image_as_base64_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": t("list_dir_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("list_dir_path_desc")},
                    "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": t("search_code_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("list_dir_path_desc")},
                    "pattern": {"type": "string", "description": "Text pattern to search for."},
                    "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")},
                    "max_results": {"type": "integer", "description": "Maximum number of matches to return."}
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": t("edit_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("write_path_desc")},
                    "old_string": {"type": "string", "description": "The text to replace."},
                    "new_string": {"type": "string", "description": "The replacement text."}
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_script",
            "description": "Execute a Python script or a block of Python code. Use the 'timeout_seconds' parameter to control the maximum execution time and prevent the process from hanging indefinitely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The Python script content to execute."
                    },
                    "cwd": {
                        "type": "string",
                        "description": "The working directory for the script execution."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Maximum execution time in seconds before the process is terminated. Defaults to 120. Set a shorter timeout for quick commands to avoid waiting."
                    }
                },
                "required": ["script", "cwd"]
            }
        }
    }
]

def ensure_not_interrupted() -> None:
    if INTERRUPTION_REQUESTED:
        raise KeyboardInterrupt(t("interrupt"))

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

def build_multimodal_user_message(
    text: str,
    image_path: str | os.PathLike[str],
    max_bytes: int | None = None,
) -> dict[str, Any]:
    path = Path(image_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_bytes = path.read_bytes()
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"

    if max_bytes is None:
        max_bytes = 120_000

    if max_bytes and len(image_bytes) > max_bytes:
        try:
            import io
            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGB")
                candidate_bytes = image_bytes
                candidate_mime = mime_type
                width, height = img.size
                for scale in [1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.03, 0.01]:
                    resized = img.resize((max(16, int(width * scale)), max(16, int(height * scale))), Image.Resampling.LANCZOS)
                    for quality in [85, 70, 55, 40, 25, 15, 10]:
                        buffer = io.BytesIO()
                        resized.save(buffer, format="JPEG", quality=quality, optimize=True)
                        candidate_bytes = buffer.getvalue()
                        candidate_mime = "image/jpeg"
                        encoded_candidate = base64.b64encode(candidate_bytes).decode("ascii")
                        if len(encoded_candidate) <= max_bytes:
                            image_bytes = candidate_bytes
                            mime_type = candidate_mime
                            break
                    if len(base64.b64encode(candidate_bytes).decode("ascii")) <= max_bytes:
                        break
                else:
                    tiny = img.resize((32, 32), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    tiny.save(buffer, format="JPEG", quality=15, optimize=True)
                    image_bytes = buffer.getvalue()
                    mime_type = "image/jpeg"
                    if len(base64.b64encode(image_bytes).decode("ascii")) > max_bytes:
                        image_bytes = b"\x00"
                        mime_type = "image/jpeg"
        except Exception:
            image_bytes = image_bytes[:max(1, max_bytes // 2)]

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                },
            },
        ],
    }

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

def execute_tool_call(tool_call: Any) -> dict[str, Any]:
    name = getattr(tool_call.function, "name", "")
    args = safe_parse_tool_args(getattr(tool_call.function, "arguments", {}))
    ensure_not_interrupted()
    logger.info(t("tool_execution", name=name, args=args))

    if name == "read_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="read_file", arg="path")}
            logger.error("read_file missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("read_file result: %s", result)
            return result

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result = {
                "status": "error",
                "content": f"Unable to read as UTF-8 text: {path}\nThis file may be binary or encoded in another charset.",
            }
            logger.warning("read_file failed due to non-UTF8 content: %s", path)
            logger.info("read_file result: %s", result)
            return result
        except Exception as exc:
            result = {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
            logger.exception("read_file failed: %s", path)
            logger.info("read_file result: %s", result)
            return result

        result = {"status": "ok", "content": content}
        logger.info("read_file result: %s", result)
        return result

    if name == "write_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="write_file", arg="path")}
            logger.error("write_file missing path argument, raw args: %s", args)
            return result
        content = args.get("content", "")
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result = {"status": "ok", "content": f"Written: {path}"}
        logger.info("write_file result: %s", result)
        return result

    if name == "delete_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="delete_file", arg="path")}
            logger.error("delete_file missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("delete_file result: %s", result)
            return result
        path.unlink()
        result = {"status": "ok", "content": f"Deleted: {path}"}
        logger.info("delete_file result: %s", result)
        return result

    if name == "create_directory":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="create_directory", arg="path")}
            logger.error("create_directory missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.mkdir(parents=True, exist_ok=True)
        result = {"status": "ok", "content": f"Created directory: {path}"}
        logger.info("create_directory result: %s", result)
        return result

    if name == "delete_directory":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="delete_directory", arg="path")}
            logger.error("delete_directory missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"Directory does not exist: {path}"}
            logger.info("delete_directory result: %s", result)
            return result
        shutil.rmtree(path)
        result = {"status": "ok", "content": f"Deleted directory: {path}"}
        logger.info("delete_directory result: %s", result)
        return result

    if name == "rename_path":
        old_path = args.get("old_path")
        new_path = args.get("new_path")
        if not old_path or not new_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="rename_path", arg="old_path/new_path")}
            logger.error("rename_path missing arguments, raw args: %s", args)
            return result
        old = Path(old_path).expanduser()
        new = Path(new_path).expanduser()
        if not old.is_absolute():
            old = Path.cwd() / old
        if not new.is_absolute():
            new = Path.cwd() / new
        if not old.exists():
            result = {"status": "error", "content": f"Path does not exist: {old}"}
            logger.info("rename_path result: %s", result)
            return result
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        result = {"status": "ok", "content": f"Renamed: {old} -> {new}"}
        logger.info("rename_path result: %s", result)
        return result

    if name == "copy_file":
        source_path = args.get("source_path")
        destination_path = args.get("destination_path")
        if not source_path or not destination_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="copy_file", arg="source_path/destination_path")}
            logger.error("copy_file missing arguments, raw args: %s", args)
            return result
        source = Path(source_path).expanduser()
        destination = Path(destination_path).expanduser()
        if not source.is_absolute():
            source = Path.cwd() / source
        if not destination.is_absolute():
            destination = Path.cwd() / destination
        if not source.exists():
            result = {"status": "error", "content": f"Source file does not exist: {source}"}
            logger.info("copy_file result: %s", result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        result = {"status": "ok", "content": f"Copied: {source} -> {destination}"}
        logger.info("copy_file result: %s", result)
        return result

    if name == "read_image_as_base64":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="read_image_as_base64", arg="path")}
            logger.error("read_image_as_base64 missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"Image file does not exist: {path}"}
            logger.info("read_image_as_base64 result: %s", result)
            return result

        result = {"status": "ok", "content": json.dumps({"path": str(path), "message": "Image loaded into context for multimodal analysis."}, ensure_ascii=False)}
        logger.info("read_image_as_base64 result: %s", result)
        return result

    if name == "list_dir":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="list_dir", arg="path")}
            logger.error("list_dir missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "ok", "content": json.dumps([], ensure_ascii=False)}
            logger.info("list_dir result: %s", result)
            return result

        recursive = bool(args.get("recursive", False))
        if recursive:
            items = []
            for item in sorted(path.rglob("*")):
                try:
                    items.append(str(item.resolve()))
                except OSError:
                    items.append(str(item))
        else:
            items = sorted([str(p) for p in path.iterdir()])
        result = {"status": "ok", "content": json.dumps(items, ensure_ascii=False)}
        logger.info("list_dir result: %s", result)
        return result

    if name == "search_code":
        file_path = args.get("path") or args.get("file_path")
        pattern = args.get("pattern")
        if not file_path or not pattern:
            result = {"status": "error", "content": t("tool_missing_arg", name="search_code", arg="path/pattern")}
            logger.error("search_code missing arguments, raw args: %s", args)
            return result

        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "ok", "content": json.dumps([], ensure_ascii=False)}
            logger.info("search_code result: %s", result)
            return result

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
                if pattern in line:
                    matches.append({
                        "path": str(candidate.resolve()),
                        "line": line_number,
                        "content": line,
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        result = {"status": "ok", "content": json.dumps(matches, ensure_ascii=False)}
        logger.info("search_code result: %s", result)
        return result

    if name == "edit_file":
        file_path = args.get("path") or args.get("file_path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not file_path or old_string is None or new_string is None:
            result = {"status": "error", "content": t("tool_missing_arg", name="edit_file", arg="path/old_string/new_string")}
            logger.error("edit_file missing arguments, raw args: %s", args)
            return result

        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("edit_file result: %s", result)
            return result

        try:
            original_text = path.read_text(encoding="utf-8")
        except Exception as exc:
            result = {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
            logger.exception("edit_file failed: %s", path)
            logger.info("edit_file result: %s", result)
            return result

        if old_string not in original_text:
            result = {"status": "error", "content": f"Target text not found in file: {path}"}
            logger.info("edit_file result: %s", result)
            return result

        updated_text = original_text.replace(old_string, new_string, 1)
        path.write_text(updated_text, encoding="utf-8")
        result = {"status": "ok", "content": f"Updated: {path}"}
        logger.info("edit_file result: %s", result)
        return result

    if name == "execute_python_script":
        script = args.get("script")
        if not script:
            result = {"status": "error", "content": t("tool_missing_arg", name="execute_python_script", arg="script")}
            logger.error("execute_python_script missing script argument, raw args: %s", args)
            return result
        cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())
        script_path = Path(cwd) / "__cli_temp_script__.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        
        # 获取自定义超时，默认 120 秒，最大不超过 360 秒
        timeout_seconds = int(args.get("timeout_seconds") or 120)
        timeout_seconds = min(max(timeout_seconds, 10), TOOL_SUBPROCESS_TIMEOUT)
        
        returncode, stdout, stderr = run_subprocess_command(
            [sys.executable, str(script_path)], 
            cwd, 
            shell=False, 
            timeout=timeout_seconds
        )
        response = {
            "status": "ok" if returncode == 0 else "error",
            "content": stdout + stderr,
            "returncode": returncode,
        }
        logger.info("execute_python_script result: %s", response)
        return response


    if name in {item["function"]["name"] for item in ACTIVE_MCP_TOOL_DEFINITIONS}:
        logger.info("Executing MCP tool: %s, args: %s", name, args)
        return run_mcp_tool(name, args)

    result = {"status": "error", "content": t("unknown_tool", name=name)}
    logger.error("Unknown tool call: %s", name)
    return result

def sanitize_tool_result_for_display(result: dict[str, Any], max_len: int = 80) -> dict[str, Any]:
    """Return a shallow copy of result suitable for CLI display.

    If the 'content' field is a string longer than max_len, replace it with
    a placeholder that indicates it's omitted. For lists, replace long string
    items similarly. This avoids dumping huge text blobs to the terminal.
    """
    try:
        display = dict(result)
    except Exception:
        return {"status": "error", "content": "<unserializable result>"}

    content = display.get("content")
    if isinstance(content, str):
        if len(content) > max_len:
            display["content"] = f"<content omitted: length={len(content)} chars>"
    elif isinstance(content, list):
        new_list: list[Any] = []
        for item in content:
            if isinstance(item, str) and len(item) > max_len:
                new_list.append(f"<item omitted: length={len(item)} chars>")
            else:
                new_list.append(item)
        display["content"] = new_list
    return display

def to_tool_call_objects(tool_calls: list[Any]) -> list[Any]:
    converted: list[Any] = []
    for idx, tool_call in enumerate(tool_calls):
        if isinstance(tool_call, SimpleNamespace):
            converted.append(tool_call)
            continue

        function = getattr(tool_call, "function", None)
        if function is None:
            continue

        converted.append(
            SimpleNamespace(
                id=getattr(tool_call, "id", f"tool_call_{idx}"),
                type=getattr(tool_call, "type", "function"),
                function=SimpleNamespace(
                    name=getattr(function, "name", ""),
                    arguments=getattr(function, "arguments", {}),
                    description=getattr(function, "description", ""),
                ),
            )
        )
    return converted

def parse_tool_calls_from_content(content: str | None) -> list[Any]:
    if not content:
        return []

    text = content.strip()
    if not text:
        return []

    decoded_calls: list[Any] = []
    start = 0
    while True:
        start = text.find("{", start)
        if start == -1:
            break

        try:
            parsed, end = json.JSONDecoder().raw_decode(text[start:])
        except Exception:
            start += 1
            continue

        if isinstance(parsed, dict) and {"name", "arguments"}.issubset(parsed.keys()):
            name = str(parsed.get("name") or "").strip()
            arguments = parsed.get("arguments") or {}
            if name:
                decoded_calls.append(
                    SimpleNamespace(
                        id=f"content_tool_{len(decoded_calls)}",
                        type="function",
                        function=SimpleNamespace(name=name, arguments=arguments),
                    )
                )
            start += end
            continue

        start += 1

    return decoded_calls

def get_content_from_tool_calls(tool_calls: List) -> str:
    """
    Extract the `content` field from a list of tool call objects
    (as returned by OpenAI's ChatCompletionMessage.tool_calls).

    Args:
        tool_calls: List[ChatCompletionMessageFunctionToolCall] or similar.
                   Each item should have a `function.arguments` attribute
                   which is a JSON string containing a dict with a `content` key.

    Returns:
        The extracted `content` string if found, otherwise an empty string ("").
    """
    if not tool_calls:
        return ""

    for tool_call in tool_calls:
        try:
            # 兼容 dict 形式（比如有些 mock / 测试数据）
            if isinstance(tool_call, dict):
                func = tool_call.get("function")
                if not func:
                    continue
                # func 可能是 dict，也可能是有 arguments 属性的对象
                if isinstance(func, dict):
                    arguments_str = func.get("arguments")
                else:
                    arguments_str = getattr(func, "arguments", None)
            else:
                # OpenAI SDK 对象：ChatCompletionMessageFunctionToolCall
                func = getattr(tool_call, "function", None)
                if func is None:
                    continue
                # Function.arguments 是一个 JSON 字符串
                arguments_str = getattr(func, "arguments", None)

            if not arguments_str:
                continue

            # arguments 本身就是 JSON 字符串，需要 parse
            if isinstance(arguments_str, str):
                arguments = json.loads(arguments_str)
            elif isinstance(arguments_str, dict):
                arguments = arguments_str
            else:
                # 其他类型直接放弃
                continue

            # 真正取 content 字段
            if isinstance(arguments, dict) and "content" in arguments:
                return arguments["content"]

        except (json.JSONDecodeError, TypeError, AttributeError):
            # 解析失败或结构异常，忽略这条 tool_call
            continue

    return ""
