"""Local browser server for the interactive one-human Werewolf game."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from game.ai_providers import ActionProvider, CLI_BACKENDS, CLI_BACKEND_CHOICES, create_provider
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


class SetupProvider(ActionProvider):
    """Placeholder used while the browser is still on the setup page."""

    name = "setup"

    def request_action(self, visible_state, request):
        raise RuntimeError("请先在游戏设置中选择 AI 模式并开始游戏")


ENGINE: HumanGameEngine = HumanGameEngine(SetupProvider())
ACTIVE_PROVIDER_CONFIG: Optional[Dict[str, Any]] = None
DEFAULT_PROVIDER_CONFIG = {"mode": "cli", "cli": "codex", "model": None}


def memory_file() -> Path:
    return STATE_FILE.parent / ".aiwolf_long_term_memory.json"


def archive_root() -> Path:
    return STATE_FILE.parent / "game_archives"


def normalize_provider_config(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the selected backend in the save without persisting secrets."""
    payload = payload or {}
    mode = str(payload.get("provider", payload.get("mode", "cli"))).lower()
    if mode == "codex-cli":
        mode = "cli"
        payload = {**payload, "cli": "codex"}
    if mode == "codex":
        return {"mode": "bridge"}
    if mode == "cli":
        backend = str(payload.get("cli", "codex")).lower()
        if backend not in CLI_BACKEND_CHOICES:
            raise ValueError(f"未知 CLI 后端：{backend}")
        raw_model = payload.get("model")
        model = str(raw_model).strip() if raw_model is not None else ""
        model = model or None
        return {"mode": "cli", "cli": backend, "model": model}
    if mode == "api":
        raw_api_model = payload.get("api_model")
        api_model = str(raw_api_model).strip() if raw_api_model is not None else ""
        api_model = api_model or None
        api_format = str(payload.get("api_format", "json_object")).strip() or "json_object"
        if api_format not in {"json_object", "json_schema", "none"}:
            raise ValueError("API 输出格式无效")
        return {"mode": "api", "api_model": api_model, "api_format": api_format}
    if mode == "bridge":
        return {"mode": "bridge"}
    raise ValueError(f"未知 AI 模式：{mode}")


def configure_engine(config: Dict[str, Any]) -> None:
    global ENGINE, ACTIVE_PROVIDER_CONFIG
    config = normalize_provider_config(config)
    if ACTIVE_PROVIDER_CONFIG == config:
        return
    mode = config["mode"]
    if mode == "bridge":
        provider = create_provider("codex", BRIDGE_ROOT)
    elif mode == "cli":
        provider = create_provider("cli", BRIDGE_ROOT, model=config.get("model"),
                                   cli=config["cli"])
    else:
        provider = create_provider("api", BRIDGE_ROOT, api_model=config.get("api_model"),
                                   api_format=config.get("api_format"))
    ENGINE = HumanGameEngine(provider)
    ACTIVE_PROVIDER_CONFIG = config


def ensure_engine_for_state(state: Dict[str, Any]) -> None:
    configure_engine(state.get("provider_config") or DEFAULT_PROVIDER_CONFIG)


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
                "sheriff_election_delayed": False,
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
        provider_config = normalize_provider_config(payload)
        configure_engine(provider_config)
        game_mode = str(payload.get("game_mode", "real")).lower()
        if game_mode not in {"real", "test"}:
            raise ValueError("未知游戏模式")
        # 真实模式固定为随机座位、随机身份；只有测试模式读取调试选项。
        seat = int(payload.get("human_seat", 0)) if game_mode == "test" else 0
        debug_role = str(payload.get("debug_role", "random")) if game_mode == "test" else "random"
        seed_value = payload.get("seed")
        seed = int(seed_value) if seed_value not in (None, "") else None
        board = str(payload.get("board", "classic"))
        state = create_game(seat, debug_role, seed, True,
                            load_memory_store(memory_file()), board=board)
        state["provider_config"] = provider_config
        state["game_mode"] = game_mode
        return state
    if action == "new_game":
        current_config = state.get("provider_config") or ACTIVE_PROVIDER_CONFIG or DEFAULT_PROVIDER_CONFIG
        new_state = setup_state()
        new_state["provider_config"] = current_config
        return new_state
    if state.get("phase") == "setup":
        raise ValueError("请先开始新游戏")
    ensure_engine_for_state(state)
    if action == "continue":
        ENGINE.advance_ai(state)
    else:
        ENGINE.submit_human(state, payload)
    return state


def panel_state(state: Dict[str, Any]) -> Dict[str, Any]:
    result = public_state_for_human(state)
    config = state.get("provider_config")
    batch_actions = (
        config.get("mode") == "bridge" if config
        else getattr(ENGINE.provider, "batch_actions", True)
    )
    result["auto_continue"] = bool(
        not batch_actions
        and result.get("can_advance_ai")
        and (state["phase"].startswith("night_")
             or state.get("phase_data", {}).get("simultaneous"))
    )
    return result


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
            if not LOCK.acquire(timeout=0.1):
                # AI 请求会持有 LOCK，避免 GET 长时间排队。但刷新页面时仍应
                # 返回最近一次原子保存的公开状态，不能把正在进行的对局误显示为设置页。
                # 下一次轮询会在 AI 写回后拿到最新状态。
                try:
                    stale = panel_state(load_state())
                    stale["stale"] = True
                    self.send_json(stale)
                except Exception as exc:
                    self.send_json({"error": f"读取最近存档失败：{exc}"}, 503)
                return
            try:
                self.send_json(panel_state(load_state()))
            finally:
                LOCK.release()
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
                response = panel_state(state)
            self.send_json(response)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"[panel] 未处理异常: {exc}")
            self.send_json({"error": "游戏处理失败，存档未被覆盖，请查看终端日志。"}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[panel] {self.address_string()} - {fmt % args}")


def main() -> None:
    global BRIDGE_ROOT, STATE_FILE
    parser = argparse.ArgumentParser(description="启动1名真人+11名电脑的狼人杀本地面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reset", action="store_true", help="返回新游戏设置页")
    parser.add_argument("--provider", choices=["codex", "cli", "api"],
                        default=None, help=argparse.SUPPRESS)
    parser.add_argument("--bridge-dir", default=str(BRIDGE_ROOT), help="Codex文件桥接目录")
    parser.add_argument("--model", "--codex-model", dest="model", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--cli", default="codex", choices=CLI_BACKEND_CHOICES,
                        help=argparse.SUPPRESS)
    parser.add_argument("--cli-args", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--api-model", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--api-key", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--api-base-url", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--api-format", default="json_object",
                        choices=["json_object", "json_schema", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--cli-timeout", type=int, default=180, help=argparse.SUPPRESS)
    parser.add_argument("--cli-retries", type=int, default=2, help=argparse.SUPPRESS)
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开游戏网页")
    parser.add_argument("--state-file", default=str(STATE_FILE), help=argparse.SUPPRESS)
    args = parser.parse_args()
    STATE_FILE = Path(args.state_file).resolve()
    BRIDGE_ROOT = Path(args.bridge_dir).resolve()
    startup_config = None
    if args.provider:
        startup_config = normalize_provider_config({
            "provider": args.provider, "cli": args.cli, "model": args.model,
            "api_model": args.api_model, "api_format": args.api_format,
        })
    if args.reset or not STATE_FILE.exists():
        initial_state = setup_state()
        if startup_config:
            initial_state["provider_config"] = startup_config
        save_state(initial_state)
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
    print("AI 模式将在网页游戏设置中选择；关键阶段会自动保存，按 Ctrl+C 停止。")
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
