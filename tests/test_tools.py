from copilot.tools import Workspace, build_tools


def test_tools_are_small_and_named(tmp_path):
    tools = build_tools(Workspace(tmp_path))
    names = {tool.name for tool in tools}
    assert names == {
        "read_file",
        "list_files",
        "search_code",
        "write_file",
        "edit_file",
        "delete_file",
        "execute_python_script",
        "execute_command",
    }


def test_file_edit_is_exact(tmp_path):
    workspace = Workspace(tmp_path)
    tools = {tool.name: tool for tool in build_tools(workspace)}
    tools["write_file"].invoke({"path": "a.py", "content": "x = 1\n"})
    result = tools["edit_file"].invoke({"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})
    assert "Edited" in result
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
