with open('D:\\prompt-copilot-cli\\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Helper function to wrap safe_path calls in try/except for a specific tool
def wrap_safe_path(content, tool_name, line_start_pattern, num_calls=1):
    """Wrap safe_path calls in try/except for a specific tool."""
    # This is complex to do with string replacement. Let's use a different approach.
    pass

# Instead, let's just add a wrapper function that catches PermissionError
# and returns a Result object, or modify safe_path to return (path, error)

# Actually, the simplest approach: make safe_path return the path or raise a custom exception
# that we catch at the execute_tool_call level.

# Let me add a helper at the top of execute_tool_call that safely resolves paths

print("Need more comprehensive fix - let's modify the approach")

# Alternative: wrap each tool's path resolution in a helper that returns (path, error_dict or None)
# But this is getting complex. Let me just wrap each occurrence.