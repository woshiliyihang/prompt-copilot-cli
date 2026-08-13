from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
MODULES = ("config", "session", "model", "mcp", "process", "tools", "cli")
DEAD = {
    "looks_like_background_service_command",
    "get_background_process_status",
    "build_multimodal_prompt_from_image",
}
EXACT = {
    "session": {"SessionStore", "ConversationRecorder", "clear_task_memory_file", "append_task_memory_entry", "summarize_memory_value", "summarize_tool_result"},
    "cli": {"SlashCommandCompleter", "interactive_loop", "build_cli_parser", "build_bottom_toolbar_text", "show_stage", "show_tool_result"},
    "model": {"wait_for_model_call_interval", "mark_model_call_completed", "update_total_token_usage", "format_usage_summary", "format_cumulative_token_summary", "chat_once", "LAST_MODEL_CALL_COMPLETED_AT", "TOTAL_TOKEN_USAGE"},
    "mcp": {"normalize_mcp_tool_definition", "_run_mcp_session", "normalize_mcp_server_config", "normalize_mcp_server_configs", "discover_mcp_tools", "run_mcp_tool", "ACTIVE_MCP_TOOL_DEFINITIONS"},
    "process": {"looks_like_background_service_command", "_read_text_file_tail", "stream_background_process_output", "start_background_process", "get_background_process_status", "wait_for_health_check", "run_subprocess_command", "handle_execute_command"},
    "tools": {"TOOL_DEFINITIONS", "safe_parse_tool_args", "get_tool_description", "sanitize_tool_result_for_display", "execute_tool_call", "get_content_from_tool_calls", "build_multimodal_user_message"},
    "config": {"TRANSLATIONS", "t", "ensure_config", "build_client", "_format_config_field_help"},
}


def bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        result: set[str] = set()
        for target in targets:
            if isinstance(target, ast.Name):
                result.add(target.id)
        return result
    return set()


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def classify(node: ast.AST) -> str | None:
    names = bound_names(node)
    if not names:
        return None
    name = next(iter(names))
    if name in DEAD:
        return "dead"
    for module, members in EXACT.items():
        if names & members:
            return module
    low = name.lower()
    if name in {"ROOT", "MEMORY_FILE_PATH", "WORKSPACE_DIR", "LOG_DIR", "LOG_FILE", "APPLICATION_VERSION", "DEFAULT_MAX_CHAT_COUNT", "CHAT_MESSAGE_MAX_COUNT", "RE_ACTION_DELAY", "TOOL_SUBPROCESS_TIMEOUT", "MODEL_REQUEST_TIMEOUT_SECONDS", "TASK_DESCRIPTION_TARGET", "DEFAULT_MODEL_CONFIG", "CONFIG_FIELD_DESCRIPTIONS", "DEFAULT_SYSTEM_PROMPT", "UI_SYSTEM_LANGUAGE"}:
        return "config"
    if name.isupper():
        return "config"
    if any(token in low for token in ("background_process", "subprocess", "health_check", "process_tree")):
        return "process"
    if any(token in low for token in ("mcp",)):
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
    start = node.lineno - 1
    end = getattr(node, "end_lineno", node.lineno)
    return "".join(lines[start:end]).rstrip() + "\n"


def add_imports(source: str, imports: list[str]) -> str:
    if not imports:
        return source
    lines = source.splitlines(keepends=True)
    index = 0
    if lines and lines[0].startswith("from __future__ import"):
        index = 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    block = "\n".join(sorted(set(imports))) + "\n\n"
    lines.insert(index, block)
    return "".join(lines)


def main() -> None:
    source = MAIN.read_text(encoding="utf-8")
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

    # Keep helper functions with their caller's module when they are not independently classified.
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            owner = node_owner.get(id(node))
            if not owner or owner == "dead":
                continue
            for name in loaded_names(node):
                dep = nodes_by_name.get(name)
                if dep is None or id(dep) in node_owner:
                    continue
                if isinstance(dep, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                    node_owner[id(dep)] = owner
                    for bound in bound_names(dep):
                        owners[bound] = owner
                    changed = True

    # Do not move the main entry point or module-level side-effect statements.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
            continue
        node_owner.pop(id(node), None)

    module_nodes: dict[str, list[ast.AST]] = {m: [] for m in MODULES}
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

    # Build dependency imports from the original top-level definitions.
    for module, nodes in module_nodes.items():
        refs: set[str] = set()
        for node in nodes:
            refs |= loaded_names(node)
        imports: list[str] = []
        for ref in sorted(refs):
            dep_module = owners.get(ref)
            if dep_module and dep_module != module and dep_module in MODULES:
                imports.append(f"from {dep_module} import {ref}")
            elif ref in import_map and import_map[ref] not in future_imports:
                imports.append(ast.unparse(import_map[ref]))
        chunks = [ast.unparse(n) for n in future_imports]
        chunks.extend(sorted(set(imports)))
        chunks.append("")
        chunks.extend(source_slice(lines, n) for n in nodes)
        text = "\n".join(chunks).rstrip() + "\n"
        (ROOT / f"{module}.py").write_text(text, encoding="utf-8")

    # Remove moved/dead definitions from main while preserving every untouched line verbatim.
    ranges = []
    for node in tree.body:
        if id(node) in moved:
            ranges.append((node.lineno - 1, getattr(node, "end_lineno", node.lineno)))
    kept_lines: list[str] = []
    for idx, line in enumerate(lines):
        if any(start <= idx < end for start, end in ranges):
            continue
        kept_lines.append(line)
    main_text = "".join(kept_lines)

    # Main still owns orchestration, so import every moved symbol it references.
    remaining = ast.parse(main_text)
    refs = set()
    for node in remaining.body:
        refs |= loaded_names(node)
    main_imports = []
    for name in sorted(refs):
        module = owners.get(name)
        if module in MODULES:
            main_imports.append(f"from {module} import {name}")
    main_text = add_imports(main_text, main_imports)
    MAIN.write_text(main_text, encoding="utf-8")


if __name__ == "__main__":
    main()
