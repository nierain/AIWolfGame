"""Keep the panel moving without anybody clicking "继续".

The web page deliberately advances one computer action per click, so a human can
read every speech.  When nobody is watching the panel, this loop performs the
same click: it POSTs the identical ``{"action": "continue"}`` the button sends,
and it steps aside whenever the game is waiting for the human seat.

It never reads or prints hidden information -- only day, phase and a counter --
so it is safe to leave running next to a player who does not want spoilers.

Usage:
    python tools/auto_advance.py --url http://127.0.0.1:18765

Start the panel with a fixed port first (``--port 18765``); with ``--port 0``
the system picks one and this script cannot find it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict

try:  # 让日志在管道/重定向下也能实时看到，而不是攒到进程结束
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

PHASE_LABELS = {
    "setup": "设置页", "night": "夜晚", "night_wolf_chat": "狼人夜聊",
    "night_wolf_vote": "狼人刀人", "night_seer": "预言家查验", "night_guard": "守卫守护",
    "night_witch_save": "女巫用药", "night_witch_poison": "女巫毒药",
    "sheriff_campaign": "上警", "sheriff_speech": "竞选发言", "sheriff_vote": "警长投票",
    "sheriff_pk_speech": "警长PK发言", "sheriff_pk_vote": "警长PK投票",
    "day_speech": "白天发言", "day_vote": "放逐投票", "day_revote": "复投",
    "day_pk_speech": "PK发言", "last_words": "遗言",
    "post_game_speech": "赛后复盘", "mvp_vote": "MVP票选", "game_over": "已结束",
}


def call(url: str, path: str, payload: Dict[str, Any] | None = None,
         timeout: float = 60.0) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url + path, data=data,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def describe(state: Dict[str, Any]) -> str:
    phase = state.get("phase", "?")
    label = PHASE_LABELS.get(phase, phase)
    return f"第{state.get('day', '?')}天 · {label}"


def main() -> int:
    parser = argparse.ArgumentParser(description="代替人工点击「继续」，自动推进电脑玩家行动")
    parser.add_argument("--url", default="http://127.0.0.1:18765", help="面板地址")
    parser.add_argument("--interval", type=float, default=1.6, help="两次推进之间的间隔秒数")
    parser.add_argument("--max-actions", type=int, default=0, help="最多推进多少个动作；0 表示不限")
    parser.add_argument("--quiet", action="store_true", help="只打印状态变化，不打印心跳")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    done = 0
    last_note = ""
    failures = 0

    print(f"自动推进已启动：{url}（Ctrl+C 停止）")
    print("轮到你自己操作时本脚本会自动让开，你在页面上操作完它会继续。")
    print()

    while True:
        try:
            state = call(url, "/api/state")
        except urllib.error.HTTPError as exc:
            print(f"读取状态失败（HTTP {exc.code}），2 秒后重试")
            time.sleep(2)
            continue
        except Exception as exc:  # 服务没起来或端口不对
            print(f"连不上 {url}：{exc}；3 秒后重试")
            time.sleep(3)
            continue

        failures = 0
        phase = state.get("phase")

        if phase == "game_over":
            print()
            print(f"对局结束：{describe(state)}｜共自动推进 {done} 个动作")
            return 0

        if phase == "setup":
            note = "setup"
            if note != last_note:
                print("面板停在设置页，等你在页面上开始新游戏……")
                last_note = note
            time.sleep(2)
            continue

        if not state.get("can_advance_ai"):
            note = f"human-{phase}"
            if note != last_note:
                print(f"[{describe(state)}] 轮到真人操作，自动推进已让开，等你操作完继续……")
                last_note = note
            time.sleep(1.2)
            continue

        note = phase
        if note != last_note:
            print(f"[{describe(state)}] 开始推进（已完成 {done} 个动作）")
            last_note = note

        try:
            state = call(url, "/api/action", {"action": "continue"})
            done += 1
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(body).get("error", body)
            except json.JSONDecodeError:
                message = body
            failures += 1
            print(f"推进失败（HTTP {exc.code}）：{message}")
            if failures >= 5:
                print("连续失败 5 次，已停止。请检查 provider 是否可用。")
                return 1
            time.sleep(3)
            continue

        if args.max_actions and done >= args.max_actions:
            print(f"已达到 --max-actions={args.max_actions}，停止。当前 {describe(state)}")
            return 0

        if not args.quiet and done % 25 == 0:
            print(f"  …已推进 {done} 个动作，当前 {describe(state)}")

        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        print("已手动停止。")
