from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p)


def build_tools(workspace: Workspace):
    @tool
    def read_file(path: Annotated[str, "File path relative to the workspace or an absolute path."]) -> str:
        """Read a UTF-8 text file and return its contents."""
        target = workspace.path(path)
        if not target.is_file():
            return f"File not found: {target}"
        return target.read_text(encoding="utf-8", errors="replace")

    @tool
    def list_files(path: Annotated[str, "Directory path."], recursive: Annotated[bool, "Whether to recurse."] = False) -> str:
        """List files and directories in the workspace."""
        target = workspace.path(path)
        if not target.is_dir():
            return f"Directory not found: {target}"
        items = target.rglob("*") if recursive else target.iterdir()
        lines = [str(p.relative_to(workspace.root)) + ("/" if p.is_dir() else "") for p in items]
        return "\n".join(sorted(lines)[:500]) or "(empty)"

    @tool
    def search_code(
        pattern: Annotated[str, "Text to search for; plain substring matching is used."],
        path: Annotated[str, "Directory or file to search."] = ".",
        max_results: Annotated[int, "Maximum number of matches."] = 100,
    ) -> str:
        """Search source files for a text pattern and return file:line matches."""
        target = workspace.path(path)
        if not target.exists():
            return f"Path not found: {target}"
        files = [target] if target.is_file() else target.rglob("*")
        ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".pytest_cache"}
        results: list[str] = []
        for file in files:
            if len(results) >= max(1, max_results) or not file.is_file() or any(part in ignored for part in file.parts):
                continue
            try:
                for number, line in enumerate(file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern in line:
                        results.append(f"{file.relative_to(workspace.root)}:{number}: {line[:300]}")
                        if len(results) >= max(1, max_results):
                            break
            except OSError:
                continue
        return "\n".join(results) or "No matches found."

    @tool
    def write_file(path: Annotated[str, "File path."], content: Annotated[str, "Complete UTF-8 file content."]) -> str:
        """Create or replace a text file."""
        target = workspace.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {target} ({len(content)} chars)"

    @tool
    def edit_file(
        path: Annotated[str, "File path."],
        old_string: Annotated[str, "Exact existing text to replace."],
        new_string: Annotated[str, "Replacement text."],
    ) -> str:
        """Apply an exact text edit; fail instead of guessing when the target is absent or ambiguous."""
        target = workspace.path(path)
        if not target.is_file():
            return f"File not found: {target}"
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_string)
        if count != 1:
            return f"Edit rejected: expected exactly one match, found {count}."
        target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Edited {target}"

    @tool
    def delete_file(path: Annotated[str, "File path."]) -> str:
        """Delete a file or an empty directory."""
        target = workspace.path(path)
        if target.is_file():
            target.unlink()
            return f"Deleted {target}"
        if target.is_dir():
            try:
                target.rmdir()
                return f"Deleted empty directory {target}"
            except OSError:
                return "Directory is not empty; delete files first."
        return f"Path not found: {target}"

    @tool
    def execute_python_script(
        script: Annotated[str, "Python source code to execute."],
        timeout_seconds: Annotated[int, "Maximum runtime in seconds."] = 120,
    ) -> str:
        """Execute Python code in the workspace and return stdout/stderr."""
        return _run([__import__("sys").executable, "-c", script], workspace.root, timeout_seconds)

    @tool
    def execute_command(
        command: Annotated[str, "Shell command to execute."],
        timeout_seconds: Annotated[int, "Maximum runtime in seconds."] = 120,
    ) -> str:
        """Execute a shell command in the workspace and return exit code plus output."""
        return _run(command, workspace.root, timeout_seconds, shell=True)

    return [read_file, list_files, search_code, write_file, edit_file, delete_file, execute_python_script, execute_command]


def _run(command, cwd: Path, timeout: int, shell: bool = False) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=shell,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(1, min(int(timeout), 900)),
        )
        output = (completed.stdout + completed.stderr).strip()
        if len(output) > 12000:
            output = output[-12000:]
        return f"exit_code={completed.returncode}\n{output}" if output else f"exit_code={completed.returncode}"
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or ""))[-12000:]
        return f"timeout after {timeout}s\n{output}"
    except Exception as exc:
        return f"command failed: {type(exc).__name__}: {exc}"
