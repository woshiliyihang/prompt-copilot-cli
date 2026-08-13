from __future__ import annotations
from config import ROOT
from config import t
from datetime import datetime, timezone
from mcp import console
from pathlib import Path
from rich.panel import Panel
from tools import logger
from typing import Any
import os
import platform
import signal
import subprocess
import threading
import time
import urllib.request
import uuid

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

def stream_background_process_output(process: subprocess.Popen[Any], log_path: str | os.PathLike[str]) -> None:
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

def start_background_process(command: Any, cwd: str, timeout_seconds: int | None = None, output_log_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    log_path = Path(output_log_path).expanduser() if output_log_path else ROOT / "logs" / "background" / f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    startup_kwargs: dict[str, Any] = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
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

    output_thread = threading.Thread(target=_drain_output, daemon=True)
    output_thread.start()

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
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    
    # 跨平台进程隔离配置
    popen_kwargs = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,  # 根治等待输入导致的卡死！
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True # 创建新进程组，便于后续杀整棵树

    try:
        proc = subprocess.Popen(command, shell=shell, **popen_kwargs)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning(t("tool_subprocess_failed"), cwd, safe_cwd, exc)
        proc = subprocess.Popen(command, cwd=str(Path.cwd()), shell=shell, **popen_kwargs)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 超时后杀掉整个进程树，防止孤儿进程
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=3)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        finally:
            try: proc.kill()
            except: pass
        return -1, "", f"Command timed out after {timeout} seconds"
    except KeyboardInterrupt:
        # 处理用户 Ctrl+C 中断
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        raise

    return proc.returncode, stdout or "", stderr or ""

def handle_execute_command(args):
    command = args.get("command")
    if not command:
        return {"status": "error", "content": "Missing 'command' argument."}
    
    cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())
    
    # 解析命令类型参数
    is_background = args.get("background", False)
    
    # ================= 路径 A: 常驻服务 (保留你原有的优秀逻辑) =================
    if is_background:
        output_log_path = args.get("output_log_path") or args.get("log_path")
        if not output_log_path:
            output_log_path = ROOT / "logs" / "background" / f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
            
        result = start_background_process(command, cwd, timeout_seconds=None, output_log_path=output_log_path)
        
        if result.get("status") == "ok":
            health_check_url = args.get("health_check_url")
            if health_check_url:
                try:
                    ready = wait_for_health_check(str(health_check_url), timeout_seconds=int(args.get("health_check_timeout", 20)))
                    result["health_check_ready"] = ready
                    result["content"] = f"Started background process (pid={result['pid']})" + (" and health check succeeded." if ready else " but health check did not succeed yet.")
                except Exception:
                    result["health_check_ready"] = False
                    result["content"] = f"Started background process (pid={result['pid']})"
            
            time.sleep(0.5) # 防止竞态
            result["output_tail"] = _read_text_file_tail(output_log_path, max_chars=4000)
            return result
        return result
        
    # ================= 路径 B: 一次性命令 (新增 Sentinel 同步等待逻辑) =================
    else:
        timeout_seconds = int(args.get("timeout_seconds") or args.get("timeout") or 120)
        
        # 生成唯一哨兵并组装命令 (跨平台适配错误码输出)
        sentinel = f"@@CMD_DONE_{uuid.uuid4().hex}@@"
        if platform.system() == "Windows":
            full_cmd = f"{command}\necho {sentinel} %errorlevel%\n"
        else:
            full_cmd = f"{command}\necho {sentinel} $?\n"
        
        # 构建启动参数 (跨平台适配进程组)
        popen_kwargs = {
            "shell": True,
            "cwd": str(cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
            
        process = subprocess.Popen(full_cmd, **popen_kwargs)
        
        output_lines = []
        timed_out = False
        start_time = time.time()
        
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    if time.time() - start_time > timeout_seconds:
                        timed_out = True
                        break
                    continue
                
                if sentinel in line:
                    # 提取退出码
                    try:
                        exit_code = int(line.split(sentinel)[-1].strip().rstrip())
                    except:
                        exit_code = -1
                    break
                else:
                    output_lines.append(line)
                    if len(output_lines) > 5000:
                        output_lines.pop(0)
                        
                if time.time() - start_time > timeout_seconds:
                    timed_out = True
                    break
        finally:
            # 超时或完成后，杀掉整个进程树 (跨平台适配)
            if process.poll() is None:
                try:
                    if platform.system() == "Windows":
                        # Windows: taskkill 强杀整个进程树
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], 
                                      capture_output=True, timeout=5)
                    else:
                        # Unix: 杀整个进程组
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        time.sleep(1)
                        if process.poll() is None:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass
        
        content = "".join(output_lines)[-4000:]
        
        if timed_out:
            return {
                "status": "timeout",
                "exit_code": None,
                "content": f"{content}\n[ERROR] Command timed out after {timeout_seconds} seconds and was forcefully terminated.\n[Hint] If this is a dev server, please use background=true."
            }
        else:
            return {
                "status": "success" if exit_code == 0 else "error",
                "exit_code": exit_code,
                "content": content
            }
