"""Bridge worker command line for the ``codex`` Provider.

The interactive panel can run with ``--provider codex``: every computer action is
written to ``codex_bridge/tasks/<id>.json`` and the game safely pauses until a
matching ``codex_bridge/responses/<id>.json`` appears. This tool is the worker
side of that protocol.

It only ever reads the isolated ``visible_state`` that the task contains, so a
worker can never see more than the acting seat is allowed to see.

Commands
--------
``pending``            list task ids that still have no response
``wait``               block until an unanswered task shows up, then print it
``show <id>``          print one compact, human readable task view
``reply <id> <json>``  validate and write ``responses/<id>.json``
``stats``              task / response counters
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "codex_bridge"

SPEECH_ACTIONS = {"speech", "pk_speech", "campaign_speech", "last_words", "postgame_speech", "wolf_chat"}
REQUIRED_TARGET = {"wolf_kill", "charm", "guard", "divine", "sheriff_recommend", "mvp_vote"}
OPTIONAL_TARGET = {"vote", "witch_poison", "sheriff_transfer", "knight_decide"}
BOOL_ACTIONS = {"campaign": "join", "withdraw": "withdraw", "witch_save": "use"}


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(text)


def tasks_dir() -> Path:
    return BRIDGE / "tasks"


def responses_dir() -> Path:
    return BRIDGE / "responses"


def load_task(task_id: str) -> Dict[str, Any]:
    path = tasks_dir() / f"{task_id}.json"
    if not path.exists():
        raise SystemExit(f"找不到任务 {task_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def unanswered_ids() -> List[str]:
    if not tasks_dir().exists():
        return []
    ids = []
    for path in sorted(tasks_dir().glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not (responses_dir() / path.name).exists():
            ids.append(path.stem)
    return ids


def _trim(text: Any, limit: int = 300) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def format_task(payload: Dict[str, Any]) -> str:
    view = payload["visible_state"]
    request = payload["request"]
    self_info = view["self"]
    private = view.get("private", {})
    players = {p["seat"]: p for p in view.get("players", [])}
    seat = self_info["seat"]

    lines: List[str] = []
    lines.append(f"TASK {payload['task_id']}")
    lines.append(
        f"ACT actor={request.get('actor')} action={request.get('action')} "
        f"seq={request.get('sequence')} allowed={request.get('allowed_targets', [])}"
    )
    lines.append(f"PROMPT {request.get('prompt', '')}")
    alive = [p["seat"] for p in view.get("players", []) if p["alive"]]
    dead = [p["seat"] for p in view.get("players", []) if not p["alive"]]
    me = players.get(seat, {})
    lines.append(
        f"SELF {seat}号 {self_info.get('role_label')} "
        f"({'存活' if self_info.get('alive') else '已出局'}) 第{view.get('day')}天 {view.get('phase')}"
    )
    lines.append(f"ALIVE {alive}  DEAD {dead}  警长 {view.get('sheriff')}")
    campaigning = [p["seat"] for p in view.get("players", []) if p.get("campaigning")]
    if campaigning:
        lines.append(f"上警 {campaigning}")
    revealed = {p["seat"]: p.get("revealed_role") for p in view.get("players", [])
                if p.get("revealed_role")}
    if revealed:
        lines.append(f"已亮身份 {revealed}")
    if view.get("winner"):
        final_roles = {p["seat"]: p.get("role") for p in view.get("players", [])}
        lines.append(f"最终身份 {final_roles}")

    if private:
        lines.append("PRIVATE")
        if "wolf_teammates" in private:
            lines.append(f"  狼队友 {private['wolf_teammates']}")
        for key in ("seer_checks", "last_guarded", "charmed_player", "medicine", "poison",
                    "duel_available", "tonight_wolf_target"):
            if key in private:
                lines.append(f"  {key}={private[key]}")
        for item in private.get("wolf_chat", []):
            lines.append(f"  狼聊 {item.get('speaker')}号: {_trim(item.get('text'), 400)}")

    if view.get("public_votes"):
        lines.append(f"公开票型 {view['public_votes']}")
    if view.get("vote_summary"):
        lines.append(f"票型汇总 {view['vote_summary']}")
    if view.get("last_duel"):
        lines.append(f"骑士决斗 {view['last_duel']}")

    memory = view.get("memory", {}) or {}
    lines.append(
        f"MEM 人格={memory.get('personality')} 策略={memory.get('strategy')} "
        f"怀疑={memory.get('beliefs', {}).get('suspects')} "
        f"信任={memory.get('beliefs', {}).get('trusted')}"
    )
    if memory.get("notes"):
        for note in memory["notes"][-4:]:
            lines.append(f"  笔记 d{note.get('day')} {note.get('speaker')}号: {_trim(note.get('text'), 200)}")

    history = view.get("history", [])
    tail = history[-24:]
    if len(history) > len(tail):
        lines.append(f"SEEN 最近{len(tail)}条（更早{len(history) - len(tail)}条已省略）")
    else:
        lines.append("SEEN 全部")
    for event in tail:
        speaker = f"{event.get('speaker')}号" if event.get("speaker") else "法官"
        lines.append(
            f"  #{event.get('id')} d{event.get('day')}/{event.get('phase')}/{event.get('kind')} "
            f"{speaker}: {_trim(event.get('text'), 400)}"
        )

    own = memory.get("own_speeches") or []
    if own:
        lines.append(f"自己最近发言: {_trim(own[-1], 200)}")
    if view.get("winner"):
        lines.append(f"WINNER {view['winner']}")
    return "\n".join(lines)


def validate(intent: Dict[str, Any], request: Dict[str, Any]) -> None:
    action = request["action"]
    if intent.get("action") != action:
        raise SystemExit(f"行动类型必须是 {action}，收到 {intent.get('action')}")
    allowed = request.get("allowed_targets", [])
    if action in REQUIRED_TARGET | OPTIONAL_TARGET:
        target = intent.get("target")
        if target is not None and target not in allowed:
            raise SystemExit(f"非法目标 {target}，可选 {allowed}")
        if target is None and action in REQUIRED_TARGET:
            raise SystemExit("该行动必须选择目标")
    if action in SPEECH_ACTIONS and not str(intent.get("text") or "").strip():
        if action != "wolf_chat":
            raise SystemExit("该行动必须提供发言文本")
    if action == "mvp_vote" and not str(intent.get("text") or "").strip():
        raise SystemExit("MVP票选必须提供简短理由")
    if action == "postgame_speech":
        impressions = intent.get("player_impressions")
        if not isinstance(impressions, list) or not 2 <= len(impressions) <= 4:
            raise SystemExit("赛后复盘必须在 player_impressions 中评价2至4位选手")
    field = BOOL_ACTIONS.get(action)
    if field and not isinstance(intent.get(field), bool):
        raise SystemExit(f"{action} 需要布尔字段 {field}")
    if action == "sheriff_order" and intent.get("direction") not in {"forward", "reverse"}:
        raise SystemExit("direction 必须是 forward 或 reverse")


def cmd_pending(_args: argparse.Namespace) -> int:
    ids = unanswered_ids()
    _out("\n".join(ids) if ids else "（没有待处理任务）")
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    total = len(list(tasks_dir().glob("*.json"))) if tasks_dir().exists() else 0
    done = len(list(responses_dir().glob("*.json"))) if responses_dir().exists() else 0
    _out(f"tasks={total} responses={done} unanswered={len(unanswered_ids())}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _out(format_task(load_task(args.task_id)))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.time() + args.timeout
    while True:
        ids = unanswered_ids()
        if ids:
            _out(format_task(load_task(ids[0])))
            return 0
        if time.time() >= deadline:
            _out(f"TIMEOUT 等待 {args.timeout}s 没有新任务（可能轮到真人操作，或游戏已结束）")
            return 3
        time.sleep(args.interval)


def cmd_reply(args: argparse.Namespace) -> int:
    payload = load_task(args.task_id)
    intent = json.loads(args.json)
    validate(intent, payload["request"])
    responses_dir().mkdir(parents=True, exist_ok=True)
    path = responses_dir() / f"{args.task_id}.json"
    path.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
    _out(f"OK 已写回 {path.name} -> {json.dumps(intent, ensure_ascii=False)}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """Answer a whole run of same-action tasks, then hand control back."""
    intents = json.loads(args.intents)
    fallback = json.loads(args.default) if args.default else None
    auto = json.loads(args.auto) if args.auto else {}
    target = args.action
    deadline = time.time() + args.idle
    handled = 0
    while True:
        ids = unanswered_ids()
        if not ids:
            if time.time() >= deadline:
                _out(f"IDLE {args.idle}s 内没有新任务（已处理 {handled} 个）")
                return 3
            time.sleep(0.3)
            continue
        payload = load_task(ids[0])
        request = payload["request"]
        action, actor = request["action"], request["actor"]
        if action != target:
            if action in auto:
                validate(auto[action], request)
                (responses_dir() / f"{ids[0]}.json").write_text(
                    json.dumps(auto[action], ensure_ascii=False, indent=2), encoding="utf-8")
                _out(f"AUTO {actor}号 {action} -> {json.dumps(auto[action], ensure_ascii=False)}")
                deadline = time.time() + args.idle
                continue
            _out(f"STOP 下一个动作是 {action}（本次已处理 {handled} 个）")
            _out(format_task(payload))
            return 0
        intent = intents.get(str(actor), fallback)
        if intent is None:
            _out(f"STOP {actor}号 没有预置意图（本次已处理 {handled} 个）")
            _out(format_task(payload))
            return 0
        validate(intent, request)
        (responses_dir() / f"{ids[0]}.json").write_text(
            json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
        handled += 1
        _out(f"OK {actor}号 {action} -> {json.dumps(intent, ensure_ascii=False)}")
        deadline = time.time() + args.idle


def cmd_intent_schema(args: argparse.Namespace) -> int:
    payload = load_task(args.task_id)
    request = payload["request"]
    action = request["action"]
    fields: Dict[str, Any] = {"action": action, "target": None, "text": None,
                              "join": None, "withdraw": None, "use": None, "direction": None,
                              "player_impressions": None}
    _out(json.dumps({
        "request_action": action,
        "allowed_targets": request.get("allowed_targets", []),
        "template": fields,
        "notes": ("speech 类动作必须填 text；wolf_chat 可留空表示只听队友；"
                  "可选目标动作用 null 表示放弃；campaign/withdraw/witch_save 必须填对应布尔字段。"
                  if action in SPEECH_ACTIONS or action in BOOL_ACTIONS else ""),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex 文件桥接的 Worker 端工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pending").set_defaults(func=cmd_pending)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    show = sub.add_parser("show")
    show.add_argument("task_id")
    show.set_defaults(func=cmd_show)

    wait = sub.add_parser("wait")
    wait.add_argument("--timeout", type=float, default=120.0)
    wait.add_argument("--interval", type=float, default=1.5)
    wait.set_defaults(func=cmd_wait)

    reply = sub.add_parser("reply")
    reply.add_argument("task_id")
    reply.add_argument("json")
    reply.set_defaults(func=cmd_reply)

    batch = sub.add_parser("batch")
    batch.add_argument("--action", required=True, help="只处理这种动作，遇到别的动作就停下")
    batch.add_argument("--intents", required=True,
                       help='JSON 对象：{"座位号": 行动对象}，例如 {"3":{"action":"campaign","join":true}}')
    batch.add_argument("--idle", type=float, default=30.0, help="连续多久没有新任务就停下")
    batch.add_argument("--default", default=None,
                       help="没有预置意图的座位用这个行动对象兜底")
    batch.add_argument("--auto", default=None,
                       help='JSON 对象：{"动作名": 行动对象}；遇到这些动作直接吞掉并继续（用于处理不会被消费的杂项任务）')
    batch.set_defaults(func=cmd_batch)

    schema = sub.add_parser("schema")
    schema.add_argument("task_id")
    schema.set_defaults(func=cmd_intent_schema)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
