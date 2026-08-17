from copilot.agent import AgentRuntime


def test_filter_think_tags(tmp_path, monkeypatch):
    # Create a minimal AgentRuntime-like object
    class Dummy:
        def __init__(self):
            pass

    rt = AgentRuntime.__new__(AgentRuntime)
    # Attach the method under test
    rt._filter_think_tags = AgentRuntime._filter_think_tags.__get__(rt, AgentRuntime)

    sample = (
        "Here is an answer. <think>internal thought: do not show</think>\nAnd more output."
    )
    assert "internal thought" not in rt._filter_think_tags(sample)

    sample2 = "Start <think/>mid</think> end"
    assert "mid" not in rt._filter_think_tags(sample2)

    sample3 = "Visible text <think/>"  # standalone
    assert "think" not in rt._filter_think_tags(sample3)
