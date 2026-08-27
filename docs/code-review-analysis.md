# main.py 问题分析与优化建议

> 前提：保持单文件架构、不做沙盒隔离。行号基于当前版本。

## 一、明确 Bug（高优先级，建议先修）

### 1. f-string 嵌套引号导致 Python 3.12 以下无法运行
```python
# main.py:132
extra_text = f" | {", ".join(extra_items)}" if extra_items else ""
```
嵌套同类引号是 PEP 701（Python 3.12+）语法，3.8~3.11 直接 `SyntaxError`。
**修复**：改为 `f" | {', '.join(extra_items)}"`，或确认 `pyproject.toml` 中 `requires-python >= 3.12`。

### 2. 消息截断可能拆散 tool_calls / tool 消息对，触发 API 400
```python
# main.py:2008-2014
conversation_messages = [m for m in messages if m.get("role") != "system"]
conversation_messages = conversation_messages[-CHAT_MESSAGE_MAX_COUNT:]
```
当单轮工具调用产生的消息超过 `CHAT_MESSAGE_MAX_COUNT`（默认 12）时，截断边界可能落在 `assistant(tool_calls)` 与其对应 `role:tool` 结果之间，OpenAI 兼容后端会报 `tool message must follow tool_calls` 类错误。
**修复**：按"块"截断——保证截断点必须是 user / 普通 assistant 消息，不切断 tool_calls 与 tool 结果的配对。

### 3. 超时分支永远捕不到
```python
# main.py:1793
except asyncio.TimeoutError:
```
同步 OpenAI 客户端超时抛的是 `openai.APITimeoutError`（继承自 `APIConnectionError`），不是 `asyncio.TimeoutError`，实际落入通用 `Exception` 分支。
**修复**：改为捕获 `openai.APITimeoutError` / `TimeoutError`。

### 4. 死代码 + 未定义翻译键
```python
# main.py:669
os_name = platform.system() or t("unknown") if False else platform.system() or 'Unknown'
```
`if False else` 是残留死代码，且 `"unknown"` 不在 `TRANSLATIONS` 中。
**修复**：`os_name = platform.system() or 'Unknown'`。

### 5. 后台进程日志文件句柄泄漏
```python
# main.py:1226 / 1288
log_file = open(log_path, "w", ...)
# 运行成功的分支永不 close
```
每个后台进程泄漏一个文件句柄；长会话多次启动服务会耗尽句柄，Windows 上还可能阻止日志被清理。
**修复**：`Popen` 返回后立即 `log_file.close()`——子进程已继承句柄，输出仍会写入文件，父进程无需持有。

### 6. 后台脚本 tempfile 删除竞态
```python
# main.py:1643-1648
finally:
    script_path.unlink(missing_ok=True)
```
`background=true` 分支返回后立即删除临时 `.py`，Windows 上 detached 进程可能尚未打开该脚本导致启动失败。
**修复**：后台模式不删除脚本（写入固定目录如 `ROOT/logs/scripts/` 并定期清理），或等 startup 探测确认存活后再删。

### 7. 模型调用错误被当作正常回复写入会话
```python
# main.py:1812-1814, 2034-2040
assistant_message = SimpleNamespace()
assistant_message.content = user_msg  # 错误提示文本
```
`chat_once` 出错时返回的 SimpleNamespace 内容（如"配额不足"）会被 `run_agent` 当作模型答案存进 `history` 和 `recent_conversations.md`，污染后续上下文。
**修复**：给返回对象加 `is_error=True` 标记，`run_agent` 遇到则不入库、直接 return。

## 二、健壮性问题（中优先级）

### 8. `read_file` 无大小上限
大文件（日志、数据集）整体进上下文，直接撑爆 token。
**修复**：限制如 256KB，超限返回前 N 字节 + `...(truncated, total=X bytes)`。

### 9. 文件编码只支持 UTF-8
中文 Windows 环境大量 GBK 文件，`read_file`/`search_code` 全部失败。
**修复**：UTF-8 失败后 fallback `gbk`（在返回中注明编码）。

### 10. `edit_file` 静默替换第一处匹配
`old_string` 出现多次时替换的不是模型预期位置，产生隐蔽错误。
**修复**：统计出现次数，>1 次报错要求提供更长上下文。

### 11. 写文件非原子
`write_file`/`edit_file` 直接覆盖，崩溃会留下半截文件。
**修复**：临时文件 + `os.replace()` 原子替换。

### 12. `_filter_think_tags` 处理不了未闭合标签
模型输出 `开头的 think 标签后没有闭合时整个标签残留。
**修复**：末尾补一步——若存在 `<think` 开头而无闭合，丢弃从该标签到结尾的内容。

### 13. `--lang` 未校验
`-l fr` 等值静默回退英文。**修复**：`choices=["zh", "en"]`。

### 14. `parse_tool_calls_from_content` 可能误判
模型正文里恰好输出含 `name`+`arguments` 键的 JSON 会被误执行为工具调用。
**修复**：要求明确标记（如 `tool_call:` 前缀）或仅当正文无其他实质内容时启用。

## 三、性能问题

### 15. MCP 工具每次调用重建会话（最大性能瓶颈）
```python
# main.py:1115
return asyncio.run(_invoke())
```
每次 `run_mcp_tool` 重新走 `_run_mcp_session`（stdio 默认 `npx -y bing-cn-mcp`，每次冷启动数秒）。一次任务几十次工具调用浪费大量时间。
**修复**：为每个 server 维护持久 `ClientSession`（启动时建立、退出时关闭）。这是收益最大的单项优化。

### 16. MCP 发现串行
`_discover_all` 逐个 server 顺序连接。**修复**：`asyncio.gather` 并行发现。

### 17. `list_dir(recursive=True)` 无忽略规则
碰上 `node_modules`/`.git` 会输出数万条路径。**修复**：默认忽略常见目录，加 `ignore` 参数。

### 18. 日志 I/O 过量
所有工具结果全文 `logger.info`，日志膨胀且拖慢执行。**修复**：日志中截断（如 500 字符），完整结果仅 debug 模式记录。

## 四、i18n 缺陷

### 19. 工具描述在 import 时固化
```python
# main.py:338-535
TOOL_DEFINITIONS = [...]  # t() 在模块加载时以 en 求值
```
`UI_SYSTEM_LANGUAGE = args.lang` 在其后才执行，`-l zh` 时工具描述、发给模型的 schema 描述仍固定英文。
**修复**：改为函数 `build_tool_definitions()`，在设置语言后构建。

## 五、可维护性（保持单文件前提下）

### 20. `execute_tool_call` 约 350 行 if 链
11 个工具全部平铺。**修复**：dispatch 字典 + 每工具一个 `tool_read_file(args) -> dict` 函数；`path` 参数校验样板可提取为公共函数。

### 21. 魔法数字散落
`MAX_AGENT_ITERATIONS=50`、`max_bytes=120_000`、`max_len=80/240/10000/1200`、`max_rounds=188/50` 等硬编码。**修复**：提为具名常量，`MAX_AGENT_ITERATIONS` 可暴露为 CLI 参数。

### 22. 杂项清理
- `from types import SimpleNamespace`（31 行）与 `import re`（32 行）位置混乱，imports 应分组归位。
- `typing.List` 仅一处使用，改 `list`。
- `ensure_config` 直接改写全局 `DEFAULT_MODEL_CONFIG`，副作用隐蔽，应返回新 dict。

## 六、交互体验

### 23. Ctrl+C 语义不一致
欢迎语说"Ctrl+C 可中断当前执行"，但输入提示符处按 Ctrl+C 直接退出整个程序（2278-2280 行）。
**修复**：提示符处首次 Ctrl+C 只清空当前输入并提示"再按一次退出"。

### 24. `plan_user_request` 无开关
每次输入都多一次模型调用做规划，简单指令（"列出目录"）纯属浪费。
**修复**：加 `--no-plan` 开关，或按输入复杂度启发式跳过。

---

## 修复优先级建议

| 批次 | 内容 | 理由 |
|------|------|------|
| P0（正确性） | #1 #2 #3 #7 | 影响能否运行 / 核心流程稳定性 |
| P1（可靠性） | #5 #6 #8 #10 #11 | 数据完整性与资源泄漏 |
| P2（性能） | #15 #16 #18 | MCP 会话复用收益最大 |
| P3（体验/维护） | #19 #20 #23 其余 | 渐进重构，不改变行为 |
