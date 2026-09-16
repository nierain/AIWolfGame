"""Local browser server for the interactive one-human Werewolf game."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from game.ai_providers import create_provider
from game.ai_profiles import archive_completed_game, ensure_profiles, load_memory_store
from game.human_game import (
    HumanGameEngine,
    create_game,
    migrate_legacy_postgame,
    public_state_for_human,
    setup_state,
)


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
STATE_FILE = ROOT / ".panel_game_state.json"
BRIDGE_ROOT = ROOT / "codex_bridge"
LOCK = threading.Lock()
ENGINE: HumanGameEngine


def memory_file() -> Path:
    return STATE_FILE.parent / ".aiwolf_long_term_memory.json"


def archive_root() -> Path:
    return STATE_FILE.parent / "game_archives"


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return setup_state()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("schema") == 2:
            changed = False
            defaults = {
                "sheriff_candidates": [], "sheriff_election_players": [],
                "vote_summary": {}, "last_duel": None,
                "postgame_impressions": {}, "mvp_votes": {}, "mvp_result": {},
            }
            for key, value in defaults.items():
                if key not in state:
                    state[key] = value
                    changed = True
            if state.get("phase") == "post_game_speech":
                prompt = ("赛后身份已经全部公开，请先以最终身份表为准，说说自己的感想、关键判断和整局思路；"
                          "再自然评价2至4位给你留下特别印象的选手，说明具体原因。")
                if state.get("phase_data", {}).get("prompt") != prompt:
                    state["phase_data"]["prompt"] = prompt
                    if state.get("pending"):
                        state["pending"]["prompt"] = prompt
                    changed = True
            changed = migrate_legacy_postgame(state) or changed
            changed = ensure_profiles(state, load_memory_store(memory_file())) or changed
            if changed:
                save_state(state)
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return setup_state()


def save_state(state: Dict[str, Any]) -> None:
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)
    archive_completed_game(state, memory_file(), archive_root())


def apply_action(state: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action")
    if action == "start_game":
        seat = int(payload.get("human_seat", 0))
        debug_role = str(payload.get("debug_role", "random"))
        seed_value = payload.get("seed")
        seed = int(seed_value) if seed_value not in (None, "") else None
        wolf_self_kill = str(payload.get("wolf_self_kill", "allow")).lower() != "deny"
        return create_game(seat, debug_role, seed, wolf_self_kill,
                           load_memory_store(memory_file()))
    if action == "new_game":
        return setup_state()
    if state.get("phase") == "setup":
        raise ValueError("请先开始新游戏")
    if action == "continue":
        ENGINE.advance_ai(state)
    else:
        ENGINE.submit_human(state, payload)
    return state


class PanelHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        relative = urlparse(path).path.lstrip("/") or "panel_game.html"
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            return str(WEB_ROOT / "panel_game.html")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                self.send_json(public_state_for_human(load_state()))
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/action":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            with LOCK:
                state = apply_action(load_state(), payload)
                save_state(state)
                response = public_state_for_human(state)
            self.send_json(response)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"[panel] 未处理异常: {exc}")
            self.send_json({"error": "游戏处理失败，存档未被覆盖，请查看终端日志。"}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[panel] {self.address_string()} - {fmt % args}")


def main() -> None:
    global ENGINE, STATE_FILE
    parser = argparse.ArgumentParser(description="启动1名真人+11名电脑的狼人杀本地面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reset", action="store_true", help="返回新游戏设置页")
    parser.add_argument("--provider", choices=["builtin", "codex", "codex-cli"], default="builtin",
                        help="电脑玩家决策来源；默认内置离线AI")
    parser.add_argument("--bridge-dir", default=str(BRIDGE_ROOT), help="Codex文件桥接目录")
    parser.add_argument("--codex-model", default=None, help="Codex CLI 使用的模型；默认沿用Codex默认值")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开游戏网页")
    parser.add_argument("--state-file", default=str(STATE_FILE), help=argparse.SUPPRESS)
    args = parser.parse_args()
    STATE_FILE = Path(args.state_file).resolve()
    ENGINE = HumanGameEngine(create_provider(args.provider, Path(args.bridge_dir), args.codex_model))
    if args.reset or not STATE_FILE.exists():
        save_state(setup_state())
    try:
        server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    except OSError as exc:
        if args.port == 0:
            raise
        print(f"端口 {args.port} 不可用（{exc}），正在自动选择可用端口……")
        server = ThreadingHTTPServer((args.host, 0), PanelHandler)
    actual_port = server.server_address[1]
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    game_url = f"http://{browser_host}:{actual_port}"
    print(f"狼人杀面板已启动：{game_url}")
    print(f"AI Provider：{args.provider}；关键阶段会自动保存，按 Ctrl+C 停止。")
    if args.open_browser:
        webbrowser.open(game_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
