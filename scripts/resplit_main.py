from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
MODULES = ("config", "session", "model", "mcp", "process", "tools", "cli")
DEAD = {"looks_like_background_service_command", "get_background_process_status", "build_multimodal_prompt_from_image"}
MAIN_OWNED = {
    "main", "plan_user_request", "is_special_command", "run_agent", "handle_task_end_command",
    "handle_sigint", "reset_interruption_state", "INTERRUPTION_REQUESTED",
}
EXACT = {
    "session": {"SessionStore", "ConversationRecorder", "clear_task_memory_file", "append_task_memory_entry", "summarize_memory_value", "summarize_tool_result", "INTERRUPTION_REQUESTED"},
    "cli": {"SlashCommandCompleter", "interactive_loop", "build_cli_parser", "build_bottom_toolbar_text", "show_stage", "show_tool_result"},
    "model": {"wait_for_model_call_interval", "mark_model_call_completed", "update_total_token_usage", "format_usage_summary", "format_cumulative_token_summary", "chat_once", "LAST_MODEL_CALL_COMPLETED_AT", "TOTAL_TOKEN_USAGE"},
    "mcp": {"normalize_mcp_tool_definition", "_run_mcp_session", "normalize_mcp_server_config", "normalize_mcp_server_configs", "discover_mcp_tools", "run_mcp_tool", "ACTIVE_MCP_TOOL_DEFINITIONS"},
    "process": {"_read_text_file_tail", "stream_background_process_output", "start_background_process", "wait_for_health_check", "run_subprocess_command", "handle_execute_command", "resolve_execution_cwd"},
    "tools": {"TOOL_DEFINITIONS", "safe_parse_tool_args", "ensure_not_interrupted", "get_tool_description", "sanitize_tool_result_for_display", "execute_tool_call", "get_content_from_tool_calls", "build_multimodal_user_message", "parse_tool_calls_from_content", "to_tool_call_objects"},
    "config": {"TRANSLATIONS", "t", "ensure_config", "build_client", "_format_config_field_help", "logger", "console"},
}


def bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {t.id for t in targets if isinstance(t, ast.Name)}
    return set()


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def classify(node: ast.AST) -> str | None:
    names = bound_names(node)
    if not names:
        return None
    if names & MAIN_OWNED:
        return "main"
    if names & DEAD:
        return "dead"
    for module, members in EXACT.items():
        if names & members:
            return module
    name = next(iter(names))
    low = name.lower()
    if name in {"ROOT", "MEMORY_FILE_PATH", "WORKSPACE_DIR", "LOG_DIR", "LOG_FILE", "APPLICATION_VERSION", "DEFAULT_MAX_CHAT_COUNT", "CHAT_MESSAGE_MAX_COUNT", "RE_ACTION_DELAY", "TOOL_SUBPROCESS_TIMEOUT", "MODEL_REQUEST_TIMEOUT_SECONDS", "TASK_DESCRIPTION_TARGET", "DEFAULT_MODEL_CONFIG", "CONFIG_FIELD_DESCRIPTIONS", "DEFAULT_SYSTEM_PROMPT", "UI_SYSTEM_LANGUAGE"}:
        return "config"
    if name.isupper():
        return "config"
    if any(token in low for token in ("background_process", "subprocess", "health_check", "process_tree")):
        return "process"
    if "mcp" in low:
        return "mcp"
    if any(token in low for token in ("file", "directory", "image", "search_code", "tool")):
        return "tools"
    if any(token in low for token in ("interactive", "completion", "toolbar", "show_stage", "show_tool_result")):
        return "cli"
    return None


def import_bindings(tree: ast.Module) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                result[alias.asname or alias.name.split(".")[0]] = node
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    result[alias.asname or alias.name] = node
    return result


def source_slice(lines: list[str], node: ast.AST) -> str:
    return "".join(lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)]).rstrip() + "\n"


def add_imports(source: str, imports: list[str]) -> str:
    if not imports:
        return source
    lines = source.splitlines(keepends=True)
    index = 0
    if lines and lines[0].startswith("from __future__ import"):
        index = 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    lines.insert(index, "\n".join(sorted(set(imports))) + "\n\n")
    return "".join(lines)


def main() -> None:
    source = subprocess.check_output(["git", "show", "origin/main:main.py"], text=True)
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    owners: dict[str, str] = {}
    nodes_by_name: dict[str, ast.AST] = {}
    node_owner: dict[int, str] = {}

    for node in tree.body:
        names = bound_names(node)
        for name in names:
            nodes_by_name[name] = node
        owner = classify(node)
        if owner:
            node_owner[id(node)] = owner
            for name in names:
                owners[name] = owner

    # Only move helpers when they are unambiguously part of a module. Never pull
    # orchestration or mutable runtime state out of main.py.
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            owner = node_owner.get(id(node))
            if owner not in MODULES:
                continue
            for name in loaded_names(node):
                dep = nodes_by_name.get(name)
                if dep is None or id(dep) in node_owner or name in MAIN_OWNED:
                    continue
                if isinstance(dep, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                    dep_owner = classify(dep)
                    if dep_owner in MODULES:
                        node_owner[id(dep)] = dep_owner
                        for bound in bound_names(dep):
                            owners[bound] = dep_owner
                        changed = True

    module_nodes = {m: [] for m in MODULES}
    moved: set[int] = set()
    for node in tree.body:
        owner = node_owner.get(id(node))
        if owner == "dead":
            moved.add(id(node))
        elif owner in module_nodes:
            module_nodes[owner].append(node)
            moved.add(id(node))

    import_map = import_bindings(tree)
    all_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    future_imports = [n for n in all_imports if isinstance(n, ast.ImportFrom) and n.module == "__future__"]

    for module, nodes in module_nodes.items():
        refs = set().union(*(loaded_names(n) for n in nodes)) if nodes else set()
        imports: list[str] = []
        for ref in sorted(refs):
            dep_module = owners.get(ref)
            if dep_module in MODULES and dep_module != module:
                imports.append(f"from {dep_module} import {ref}")
            elif ref in import_map and import_map[ref] not in future_imports:
                imports.append(ast.unparse(import_map[ref]))
        chunks = [ast.unparse(n) for n in future_imports]
        chunks.extend(sorted(set(imports)))
        chunks.append("")
        chunks.extend(source_slice(lines, n) for n in nodes)
        (ROOT / f"{module}.py").write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")

    ranges = [(n.lineno - 1, getattr(n, "end_lineno", n.lineno)) for n in tree.body if id(n) in moved]
    kept = [line for idx, line in enumerate(lines) if not any(start <= idx < end for start, end in ranges)]
    main_text = "".join(kept)
    remaining = ast.parse(main_text)
    refs = set().union(*(loaded_names(n) for n in remaining.body)) if remaining.body else set()
    main_imports = []
    for name in sorted(refs):
        module = owners.get(name)
        if module in MODULES:
            if name in {"interactive_loop", "build_cli_parser"}:
                continue
            main_imports.append(f"from {module} import {name}")
    main_text = add_imports(main_text, main_imports)

    # CLI imports the orchestration functions from main; main imports CLI lazily
    # inside main() to avoid an import cycle while preserving the existing flow.
    if "def main(" in main_text:
        main_text = main_text.replace(
            "def main():",
            "def main():\n    from cli import build_cli_parser, interactive_loop",
            1,
        )
        if "def main(" not in main_text:
            raise RuntimeError("main() was not preserved")
    MAIN.write_text(main_text, encoding="utf-8")


if __name__ == "__main__":
    main()
