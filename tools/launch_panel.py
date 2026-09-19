"""Start (or stop) ``panel_game.py`` as a detached local process.

The interactive game has to keep running while the assistant is idle, so the
panel cannot be a child process of the assistant's shell. This launcher spawns
it detached from the current process group, writes stdout/stderr to
``logs/panel_bridge.out.log`` and records the pid in ``logs/panel_bridge.pid``.

Usage::

    python tools/launch_panel.py start --provider codex --reset
    python tools/launch_panel.py start --provider cli --cli ollama --model qwen3
    python tools/launch_panel.py status
    python tools/launch_panel.py stop
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from game.ai_providers import CLI_BACKEND_CHOICES

LOG_DIR = ROOT / "logs"
OUT_LOG = LOG_DIR / "panel_bridge.out.log"
ERR_LOG = LOG_DIR / "panel_bridge.err.log"
PID_FILE = LOG_DIR / "panel_bridge.pid"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(text)


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def url_from_log() -> str | None:
    if not OUT_LOG.exists():
        return None
    for line in OUT_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if "已启动" in line and "http" in line:
            return ("http" + line.split("http", 1)[1]).strip()
    return None


def cmd_stop(_args: argparse.Namespace) -> int:
    pid = read_pid()
    if not alive(pid):
        _out("没有正在运行的面板进程")
        return 0
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, check=False)
    PID_FILE.unlink(missing_ok=True)
    _out(f"已停止面板进程 {pid}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    pid = read_pid()
    _out(f"pid={pid} alive={alive(pid)} url={url_from_log()}")
    if ERR_LOG.exists():
        tail = ERR_LOG.read_text(encoding="utf-8", errors="replace").strip()
        if tail:
            _out("stderr: " + tail[-400:])
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pid = read_pid()
    if alive(pid):
        _out(f"面板已经在运行（pid={pid}，url={url_from_log()}）")
        return 0
    OUT_LOG.unlink(missing_ok=True)
    ERR_LOG.unlink(missing_ok=True)
    command = [
        sys.executable, "panel_game.py",
        "--port", str(args.port),
        "--provider", args.provider,
    ]
    if args.provider == "cli":
        command.extend(["--cli", args.cli])
        if args.model:
            command.extend(["--model", args.model])
    if args.open_browser:
        command.append("--open-browser")
    if args.reset:
        command.append("--reset")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with OUT_LOG.open("w", encoding="utf-8") as out, ERR_LOG.open("w", encoding="utf-8") as err:
        process = subprocess.Popen(
            command, cwd=str(ROOT), stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    for _ in range(40):
        time.sleep(0.25)
        url = url_from_log()
        if url:
            _out(f"已启动 pid={process.pid} url={url} provider={args.provider}")
            return 0
        if process.poll() is not None:
            detail = ERR_LOG.read_text(encoding="utf-8", errors="replace")[-400:]
            _out(f"启动失败，退出码 {process.returncode}\n{detail}")
            return 1
    _out(f"已启动 pid={process.pid}，但还没有解析到端口，请查看 logs/panel_bridge.out.log")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="狼人杀面板的分离式启动器")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--provider", default="codex",
                       choices=["codex", "cli", "api"])
    start.add_argument("--cli", default="codex", choices=CLI_BACKEND_CHOICES,
                       help="--provider cli 时选择本地 CLI 后端")
    start.add_argument("--model", default=None,
                       help="--provider cli 时覆盖模型名称")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--reset", action="store_true")
    start.add_argument("--open-browser", action="store_true", default=True)
    start.set_defaults(func=cmd_start)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("stop").set_defaults(func=cmd_stop)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
