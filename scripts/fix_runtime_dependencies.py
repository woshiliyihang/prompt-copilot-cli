from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

main = ROOT / 'main.py'
text = main.read_text(encoding='utf-8')
text = text.replace(
    'def main() -> None:\n    global UI_SYSTEM_LANGUAGE, DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, DEFAULT_MAX_CHAT_COUNT, CHAT_MESSAGE_MAX_COUNT\n\n    parser = build_cli_parser()',
    'def main() -> None:\n    from cli import build_cli_parser, interactive_loop\n    import config as config_module\n    import model as model_module\n    global UI_SYSTEM_LANGUAGE, DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, DEFAULT_MAX_CHAT_COUNT, CHAT_MESSAGE_MAX_COUNT\n\n    parser = build_cli_parser()',
)
text = text.replace(
    '    RE_ACTION_DELAY = int(args.request_delay)\n    DEFAULT_MAX_CHAT_COUNT = int(args.history_count)\n    CHAT_MESSAGE_MAX_COUNT = int(args.agent_messages_count)\n\n    # Set localization language\n    UI_SYSTEM_LANGUAGE = args.lang\n\n    # set up workspace directory\n    WORKSPACE_DIR = Path(args.workdir)',
    '    RE_ACTION_DELAY = int(args.request_delay)\n    DEFAULT_MAX_CHAT_COUNT = int(args.history_count)\n    CHAT_MESSAGE_MAX_COUNT = int(args.agent_messages_count)\n    config_module.RE_ACTION_DELAY = RE_ACTION_DELAY\n    config_module.DEFAULT_MAX_CHAT_COUNT = DEFAULT_MAX_CHAT_COUNT\n    config_module.CHAT_MESSAGE_MAX_COUNT = CHAT_MESSAGE_MAX_COUNT\n    model_module.RE_ACTION_DELAY = RE_ACTION_DELAY\n\n    # Set localization language\n    UI_SYSTEM_LANGUAGE = args.lang\n    config_module.UI_SYSTEM_LANGUAGE = UI_SYSTEM_LANGUAGE\n\n    # set up workspace directory\n    WORKSPACE_DIR = Path(args.workdir)\n    config_module.WORKSPACE_DIR = WORKSPACE_DIR',
)
main.write_text(text, encoding='utf-8')

cli = ROOT / 'cli.py'
text = cli.read_text(encoding='utf-8')
if 'from main import handle_task_end_command' not in text:
    text = text.replace('from __future__ import annotations\n', 'from __future__ import annotations\nfrom main import handle_task_end_command, run_agent\n', 1)
cli.write_text(text, encoding='utf-8')

model = ROOT / 'model.py'
text = model.read_text(encoding='utf-8')
text = text.replace('from cli import show_stage\n', '', 1)
marker = 'def chat_once('
if marker in text and 'from cli import show_stage' not in text:
    text = text.replace(marker, marker + '\n    from cli import show_stage', 1)
model.write_text(text, encoding='utf-8')

tools = ROOT / 'tools.py'
text = tools.read_text(encoding='utf-8')
text = text.replace('def ensure_not_interrupted() -> None:\n    if INTERRUPTION_REQUESTED:', 'def ensure_not_interrupted() -> None:\n    from main import INTERRUPTION_REQUESTED\n    if INTERRUPTION_REQUESTED:', 1)
tools.write_text(text, encoding='utf-8')
