"""Replaceable decision providers for the interactive human game.

Providers receive only the acting player's visible state and return an intent.
They never receive, or mutate, the authoritative game state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ACTION_TARGET_REQUIRED = {
    "wolf_kill", "charm", "guard", "divine", "sheriff_recommend", "mvp_vote",
}
ACTION_TARGET_OPTIONAL = ACTION_TARGET_REQUIRED | {
    "vote", "witch_poison", "witch_action", "sheriff_transfer", "knight_decide",
}
ACTION_SPEECH = {"speech", "pk_speech", "campaign_speech", "last_words", "postgame_speech"}
ROLE_LABELS = {
    "werewolf": "狼人", "wolf_beauty": "狼美人", "hidden_wolf": "觉醒隐狼",
    "seer": "预言家", "witch": "女巫", "knight": "骑士", "guard": "守卫",
    "hunter": "猎人", "mirror_maiden": "魔镜少女", "villager": "平民",
}
_ENVELOPE_KEYS = ("structured_output", "result", "response", "output", "content", "text")
_QUOTA_HINTS = ("usage limit", "rate limit", "quota", "too many requests", "429")
_AUTH_HINTS = ("unauthorized", "invalid api key", "not logged in", "authentication failed", "401")


def summarize_cli_error(detail: str, label: str, limit: int = 240) -> str:
    """Turn raw CLI stderr into one short, actionable message.

    Codex echoes the whole prompt back on failure, so the raw tail is mostly
    payload and buries the actual cause.  Prefer the explicit ``ERROR:`` lines
    the CLIs print, and name the two failures users actually hit.
    """
    text = (detail or "").strip()
    if not text:
        return f"{label}调用失败，并且没有输出任何错误信息。"
    errors = [line.strip() for line in text.splitlines()
              if line.strip().upper().startswith("ERROR:")]
    hint = errors[-1][:limit] if errors else text[-limit:]
    lowered = text.lower()
    if ("failed to initialize in-process app-server client" in lowered
            and any(token in lowered for token in ("os error 5", "access is denied", "拒绝访问"))):
        return (f"{label}启动被 Windows 拒绝访问（os error 5）。"
                "请关闭当前面板，从 Windows 正常终端或“启动狼人杀.bat”重新启动；"
                "不要从受限的 Codex 沙盒进程启动面板。存档保留，可继续原对局。")
    if any(token in lowered for token in _QUOTA_HINTS):
        return (f"{label}的额度或频率已用尽，本次行动无法完成；"
                f"请换后端后重试（--provider cli，或 --provider api）。原始提示：{hint}")
    if any(token in lowered for token in _AUTH_HINTS):
        return f"{label}未登录或凭据已失效，请先重新登录。原始提示：{hint}"
    return f"{label}调用失败：{hint}"


def action_schema(action: str) -> Dict[str, Any]:
    """The single structured-output contract that every provider must satisfy."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "const": action},
            "target": {"type": ["integer", "null"]},
            "text": {"type": ["string", "null"]},
            "join": {"type": ["boolean", "null"]},
            "withdraw": {"type": ["boolean", "null"]},
            "use": {"type": ["boolean", "null"]},
            "choice": {
                "type": ["string", "null"],
                "enum": ["none", "save", "poison", None],
            },
            "direction": {
                "type": ["string", "null"],
                "enum": ["forward", "reverse", None],
            },
            "player_impressions": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "seat": {"type": "integer", "minimum": 1, "maximum": 12},
                        "impression": {"type": "string"},
                    },
                    "required": ["seat", "impression"],
                    "additionalProperties": False,
                },
                "maxItems": 4,
            },
        },
        "required": ["action", "target", "text", "join", "withdraw", "use", "choice", "direction", "player_impressions"],
        "additionalProperties": False,
    }


def board_rules_prompt(visible_state: Dict[str, Any]) -> str:
    """Add the selected board's rules to every model request.

    The mirror board deliberately has no seer.  Keeping this correction next
    to the provider contract prevents a generic model from importing the
    classic board's seer vocabulary merely because it recognizes the game as
    Werewolf.
    """
    rules = visible_state.get("rules", {}) if isinstance(visible_state, dict) else {}
    if rules.get("board") != "镜隐迷踪":
        return ""
    role_rules = rules.get("role_rules") or (
        "镜隐迷踪板：没有预言家；魔镜少女是独立的具体身份查验信息位。"
    )
    return f"""
【本局板子规则（优先级高于通用狼人杀模板）】
{role_rules}
本板没有预言家，不能把魔镜少女称作预言家，也不能把预言家、首验、警徽流当成默认身份或技能话术。若其他玩家错误提到预言家规则，应指出这与本板不符，不要跟着错误模板发言。
魔镜少女的查验结果是目标的具体身份（如守卫、女巫、猎人、狼人或平民），不是‘好人/狼人’二分；只有 visible_state.private.mirror_peeks 中已有的结果才可以公开。其他角色要围绕自己的真实技能、公开发言、票型和具体身份查验信息判断，不能套用经典板的预言家流程。
"""


def action_prompt(action: str) -> str:
    """Role prompt shared by every provider; contains no backend-specific text."""
    postgame = ("当前是赛后复盘，胜负已经确定且全部身份已公开。players.role 是最终身份表，"
                "它比玩家在发言或遗言中的自称更可靠，必须先核对再复盘。请结合完整公开历史、"
                "自己的身份与私有经历，真诚说明感想、关键判断、行动目的、成功或失误之处以及全局思路；"
                "再挑选2至4位给你留下特别印象的其他选手，在发言中自然评价其具体表现，并把同样的压缩评价"
                "写入 player_impressions，每条不超过两句话。"
                if action == "postgame_speech" else "")
    mvp = ("当前是赛后MVP票选。全部身份已经公开，请以实际贡献和对胜负的影响为准公正投票，"
           "可以投自己；target 填候选座位，text 用一到两句话概括可核对的理由，不要写长文。"
           if action == "mvp_vote" else "")
    witch = ("当前是女巫夜间行动：choice 只能填 none、save 或 poison；每晚最多选择一瓶药。"
             "选择 save 时 target 必须是今晚狼人刀口中的一名目标，选择 poison 时 target 必须是毒杀目标，"
             "选择 none 时 target 填 null。"
             if action == "witch_action" else "")
    return f"""你正在扮演一局12人狼人杀中的一个真实玩家，现在需要完成动作 {action}。
输入JSON中的 visible_state 是你唯一知道的局面；严禁猜测或寻找未提供的隐藏身份，严禁读取文件或使用工具。
{postgame}{mvp}{witch}
结合公开发言、公开票型、你自己的身份/私有信息、历史立场、人格和策略认真判断。公开历史中的每一位玩家发言（包括真人玩家的发言）都是当前轮次的有效信息，必须先读懂并在相关行动中回应，不能把真人发言当成背景噪音或直接跳过。好人要分析发言与行为的一致性；狼人可以撒谎、悍跳、冲锋、倒钩或卖队友，但不要泄露狼队信息；狼人若在 private.wolf_chat 中看到队友已经安排战术，应优先执行该计划并在白天配合；神职要合理安排技能与信息公开时机。
memory 中的人格、说话风格和跨局摘要属于你这个固定玩家本人，请自然延续经验与性格；过去对局中的身份和结论只可作为复盘经验，不能当成当前对局的隐藏信息。
发言必须像中文狼人杀玩家，针对具体座位和已发生事件形成连贯逻辑；允许判断错误和合理改站边，但改站边时应解释原因。不要说自己是AI，不要用概率报告或模板化空话。
只返回符合输出结构的行动JSON。没有使用的字段填 null。target 只能从 request.allowed_targets 中选择；允许放弃的动作可填 null。除赛后复盘外 player_impressions 填 null。"""


def validate_intent(intent: Dict[str, Any], request: Dict[str, Any],
                    label: str = "电脑玩家") -> None:
    """Reject anything the rules engine could not legally apply."""
    action = request["action"]
    if intent.get("action") != action:
        raise ValueError(f"{label}返回了错误的动作类型，请重试。")
    allowed = request.get("allowed_targets", [])
    if action in ACTION_TARGET_OPTIONAL:
        target = intent.get("target")
        if target is None and action in ACTION_TARGET_REQUIRED:
            raise ValueError(f"{label}没有选择必需的目标，请重试。")
        if target is not None and target not in allowed:
            raise ValueError(f"{label}选择了非法目标，请重试。")
    if action in ACTION_SPEECH and not str(intent.get("text") or "").strip():
        raise ValueError(f"{label}没有给出发言，请重试。")
    if action == "mvp_vote" and not str(intent.get("text") or "").strip():
        raise ValueError(f"{label}没有给出MVP票选理由，请重试。")
    if action == "postgame_speech":
        impressions = intent.get("player_impressions")
        if not isinstance(impressions, list) or not 2 <= len(impressions) <= 4:
            raise ValueError("赛后复盘需要评价2至4位特别选手")
    if action == "campaign" and not isinstance(intent.get("join"), bool):
        raise ValueError(f"{label}没有决定是否上警，请重试。")
    if action == "withdraw" and not isinstance(intent.get("withdraw"), bool):
        raise ValueError(f"{label}没有决定是否退水，请重试。")
    if action == "witch_save" and not isinstance(intent.get("use"), bool):
        raise ValueError(f"{label}没有决定是否使用解药，请重试。")
    if action == "witch_action":
        choice = intent.get("choice")
        target = intent.get("target")
        if choice not in {"none", "save", "poison"}:
            raise ValueError(f"{label}没有选择合法的药瓶，请重试。")
        if choice == "none" and target is not None:
            raise ValueError(f"{label}选择不用药时不能选择目标，请重试。")
        if choice in {"save", "poison"} and target is None:
            raise ValueError(f"{label}使用药瓶时必须选择目标，请重试。")
    if action == "sheriff_order" and intent.get("direction") not in {"forward", "reverse"}:
        raise ValueError(f"{label}警长没有选择合法发言方向，请重试。")


def parse_action_payload(raw: str, depth: int = 0) -> Dict[str, Any]:
    """Pull one JSON action object out of whatever a CLI printed.

    CLI backends differ a lot here: ``ollama`` prints the object directly,
    Claude Code wraps it in a ``{"result": "..."}`` envelope, and weaker local
    models like to add prose or markdown fences around it.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("CLI 没有输出任何内容")
    text = raw.strip()
    candidates = [text]
    candidates.extend(match.group(1).strip()
                      for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if "action" not in data and depth < 3:
            for key in _ENVELOPE_KEYS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    try:
                        return parse_action_payload(value, depth + 1)
                    except ValueError:
                        continue
        return data
    raise ValueError("CLI 输出里没有找到 JSON 对象")


class ActionProvider(ABC):
    """Interface implemented by every AI decision backend."""

    name = "base"
    batch_actions = True

    @abstractmethod
    def request_action(
        self, visible_state: Dict[str, Any], request: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Return one proposed action, or ``None`` while an external worker runs."""


def _stable_choice(values, *parts):
    values = list(values)
    if not values:
        return None
    key = "|".join(str(part) for part in parts).encode("utf-8")
    index = int(hashlib.sha256(key).hexdigest()[:12], 16) % len(values)
    return values[index]


class BuiltinAIProvider(ActionProvider):
    """Offline fallback used by the browser game when no model is configured."""

    name = "builtin"

    def request_action(self, visible_state, request):
        action = request["action"]
        seat = visible_state["self"]["seat"]
        targets = request.get("allowed_targets", [])
        day = visible_state["day"]
        persona = visible_state.get("memory", {}).get("personality", "逻辑流")
        key = (visible_state["game_id"], day, visible_state["phase"], seat, action)

        if action == "campaign":
            role = visible_state["self"]["role"]
            wants = role in {"seer", "mirror_maiden"} or (role in {"werewolf", "wolf_beauty"} and seat % 2 == 0)
            wants = wants or persona in {"激进", "高手"} and seat % 3 == 0
            return {"action": action, "join": wants}
        if action == "withdraw":
            role = visible_state["self"]["role"]
            withdraw = role not in {"seer", "mirror_maiden", "werewolf"} and seat % 2 == 1
            return {"action": action, "withdraw": withdraw}
        if action in {"vote", "wolf_kill", "divine", "guard", "charm", "sheriff_recommend"}:
            target = self._pick_target(visible_state, targets, action, key)
            return {"action": action, "target": target}
        if action == "witch_action":
            victim = visible_state.get("private", {}).get("tonight_wolf_target")
            victims = visible_state.get("private", {}).get("tonight_wolf_targets") or (
                [victim] if victim else []
            )
            medicine = visible_state.get("private", {}).get("medicine", False)
            poison = visible_state.get("private", {}).get("poison", False)
            if medicine and victims and day <= 1:
                return {"action": action, "choice": "save", "target": victims[0]}
            if poison and day >= 2:
                target = self._pick_target(visible_state, targets, action, key)
                return {"action": action, "choice": "poison", "target": target}
            return {"action": action, "choice": "none", "target": None}
        if action == "witch_save":
            victim = visible_state.get("private", {}).get("tonight_wolf_target")
            use = bool(victim) and visible_state.get("private", {}).get("medicine", False)
            if day > 1 and victim != visible_state.get("sheriff"):
                use = False
            return {"action": action, "use": use}
        if action == "witch_poison":
            target = None
            if day >= 2 and visible_state.get("private", {}).get("poison", False):
                target = self._pick_target(visible_state, targets, action, key)
            return {"action": action, "target": target}
        if action == "knight_decide":
            target = None
            if day >= 2 or persona in {"激进", "操作型"}:
                target = self._pick_target(visible_state, targets, action, key)
            return {"action": action, "target": target}
        if action == "wolf_chat":
            target = self._pick_target(visible_state, targets, "wolf_kill", key)
            chat = visible_state.get("private", {}).get("wolf_chat", [])
            prefix = f"我听到{chat[-1]['speaker']}号的意见了。" if chat else ""
            text = prefix + (f"今晚我偏向刀{target}号，白天我会尽量和狼队拉开关系。" if target else "先听队友意见，我这轮可以做深水。")
            return {"action": action, "text": text}
        if action == "postgame_speech":
            text, impressions = self._postgame_speech(visible_state, persona)
            return {"action": action, "text": text, "player_impressions": impressions}
        if action == "mvp_vote":
            target = self._pick_mvp(visible_state, targets, key)
            return {
                "action": action,
                "target": target,
                "text": f"我投给{target}号。他在关键轮次的判断和行动对最终胜负影响最直接。",
            }
        if action in {"speech", "pk_speech", "campaign_speech", "last_words"}:
            return {"action": action, "text": self._speech(visible_state, action, persona)}
        if action == "sheriff_order":
            return {"action": action, "direction": "forward" if seat % 2 else "reverse"}
        if action == "sheriff_transfer":
            return {"action": action, "target": self._pick_target(visible_state, targets, action, key)}
        if action == "hidden_learn":
            # 隐狼第 1 晚学习：盲选一名非自己玩家
            return {"action": action, "target": _stable_choice(sorted(targets), *key)}
        if action == "hidden_blade":
            # 隐狼带刀：选一个目标；学狼人时给第二目标
            target = self._pick_target(visible_state, targets, "wolf_kill", key)
            intent = {"action": action, "target": target}
            if "second_target" in request.get("allowed_targets", []) or "双刀" in request.get("prompt", ""):
                second = [t for t in targets if t != target]
                if second:
                    intent["second_target"] = _stable_choice(sorted(second), *key)
            return intent
        if action == "hidden_skill":
            return {"action": action, "target": self._pick_target(visible_state, targets, action, key)}
        if action == "mirror_peek":
            return {"action": action, "target": self._pick_target(visible_state, targets, action, key)}
        if action == "hunter_shoot":
            # 猎人开枪：默认开（带走一个），targets 不含自己
            return {"action": action, "target": self._pick_target(visible_state, targets, action, key)}
        raise ValueError(f"内置AI不支持动作: {action}")

    def _pick_target(self, view, targets, action, key):
        targets = list(targets)
        if not targets:
            return None
        private = view.get("private", {})
        wolves = set(private.get("wolf_teammates", [])) | {view["self"]["seat"]}
        if action in {"wolf_kill", "charm"}:
            targets = [target for target in targets if target not in wolves]
            sheriff = view.get("sheriff")
            if sheriff in targets:
                return sheriff
        if action in {"vote", "sheriff_recommend"} and wolves:
            non_wolves = [target for target in targets if target not in wolves]
            if non_wolves:
                targets = non_wolves
        if action == "divine":
            checked = {item["seat"] for item in private.get("seer_checks", [])}
            unchecked = [target for target in targets if target not in checked]
            if unchecked:
                targets = unchecked
        if action == "mirror_peek":
            checked = {item["seat"] for item in private.get("mirror_peeks", [])}
            unchecked = [target for target in targets if target not in checked]
            if unchecked:
                targets = unchecked
        public_votes = view.get("public_votes", {})
        if action == "vote" and public_votes:
            leaders = sorted(targets, key=lambda target: (-public_votes.get(str(target), 0), target))
            if public_votes.get(str(leaders[0]), 0):
                return leaders[0]
        return _stable_choice(sorted(targets), *key)

    def _speech(self, view, action, persona):
        seat = view["self"]["seat"]
        role = view["self"]["role"]
        private = view.get("private", {})
        mirror_board = view.get("rules", {}).get("board") == "镜隐迷踪"
        recent = [event for event in view.get("history", []) if event.get("kind") == "speech"][-3:]
        recent_seats = [event.get("speaker") for event in recent if event.get("speaker") != seat]
        focus = recent_seats[-1] if recent_seats else next(
            (p["seat"] for p in view["players"] if p["alive"] and p["seat"] != seat), seat
        )
        if action == "campaign_speech":
            if role == "seer" and private.get("seer_checks"):
                check = private["seer_checks"][-1]
                result = "狼人" if check["is_wolf"] else "好人"
                return f"我上警是因为我底牌预言家，昨晚验了{check['seat']}号，是{result}。警徽流我会看后面的发言再定，先把验人逻辑讲清楚。"
            if role == "mirror_maiden":
                peeks = private.get("mirror_peeks", [])
                if peeks:
                    check = peeks[-1]
                    result = ROLE_LABELS.get(check.get("shown_role"), check.get("shown_role", "未知身份"))
                    return (f"我上警是因为我底牌魔镜少女，昨晚查验{check['seat']}号，具体身份是{result}。"
                            "我的查验是具体身份，不是简单的好人或狼人；后续我会继续报验人和带队。")
                return "我上警是因为我底牌魔镜少女，这是本板独立的具体身份查验信息位；我会公开报告已有查验、盘具体身份和带队。"
            if role in {"werewolf", "wolf_beauty"}:
                if mirror_board:
                    return f"我上警想替好人多拿一点信息。这个板子没有预言家，我会重点听{focus}号是否真正理解魔镜少女的具体身份查验和公开票型。"
                return f"我上警想替好人多拿一点信息。现在没有必要盲信强势发言，我会重点听{focus}号的视角和后续警徽流。"
            return f"我上警不是硬跳身份，主要想把自己的视角聊清楚。{focus}号刚才的发言我还没完全听懂，后面我会根据警徽票再站边。"
        if role == "seer" and private.get("seer_checks"):
            check = private["seer_checks"][-1]
            result = "狼人" if check["is_wolf"] else "好人"
            return f"我昨晚验了{check['seat']}号，是{result}。我现在更关注{focus}号前后逻辑有没有变化，今天先围绕验人和票型来出。"
        if role == "mirror_maiden" and private.get("mirror_peeks"):
            check = private["mirror_peeks"][-1]
            result = ROLE_LABELS.get(check.get("shown_role"), check.get("shown_role", "未知身份"))
            return (f"我是魔镜少女，昨晚查验{check['seat']}号，具体身份是{result}。"
                    f"我会围绕具体身份查验结果和公开票型组织今天的判断，重点听{focus}号怎么解释。")
        if role in {"werewolf", "wolf_beauty"}:
            return f"我现在不想跟着场上最响的声音走。{focus}号这轮给结论有点快，前面的票型也没解释干净，我会先听他怎么回头。"
        styles = {
            "激进": f"我先把票挂在{focus}号，他这轮的站边太快，像是先有答案再找理由。后置位如果认同就直接对话。",
            "保守": f"我暂时怀疑{focus}号，但还不到拍死的程度。今天把警徽票和发言变化一起看，别只靠一句话定身份。",
            "划水": f"目前我更想听{focus}号解释，其他位置还没形成完整逻辑。我先保留票，后面再跟着有效信息走。",
        }
        return styles.get(persona, f"我这轮更想看{focus}号。主要是他的结论和前面公开票型对不上，不是因为情绪；如果他能解释清楚，我可以改站边。")

    def _postgame_speech(self, view, persona):
        seat = view["self"]["seat"]
        role = view["self"]["role"]
        winner = view.get("winner")
        wolf_side = role in {"werewolf", "wolf_beauty", "hidden_wolf"}
        won = winner == ("狼人阵营" if wolf_side else "好人阵营")
        result = "赢下这局很开心" if won else "这局输了有些遗憾"
        wolves = [player["seat"] for player in view["players"]
                  if player.get("role") in {"werewolf", "wolf_beauty"}]
        memory = view.get("memory", {})
        speeches = len(memory.get("own_speeches", []))
        votes = len(memory.get("votes", []))
        if wolf_side:
            plan = f"我的整体思路是和狼队{wolves}号互相配合，同时通过站边和票型隐藏团队关系"
        elif role == "seer":
            checks = view.get("private", {}).get("seer_checks", [])
            summary = "、".join(f"{item['seat']}号{'狼' if item['is_wolf'] else '好'}" for item in checks) or "没有留下有效验人"
            plan = f"我的整体思路是围绕验人信息组织好人视角，我的验人记录是{summary}"
        elif role == "witch":
            plan = "我的整体思路是结合夜间刀口和白天站边安排解药、毒药，不让技能脱离公开逻辑"
        elif role == "guard":
            plan = "我的整体思路是从带队位置和狼人可能的刀法判断守护目标，同时避免连续守人"
        elif role == "knight":
            plan = "我的整体思路是先听发言找逻辑矛盾，再判断是否值得用唯一一次决斗验证身份"
        else:
            plan = "我的整体思路是只用公开发言和票型逐轮排坑，观察谁的立场前后不一致"
        others = [player for player in view["players"] if player["seat"] != seat]
        scores = self._performance_scores(view, [player["seat"] for player in others])
        notable = sorted(
            others, key=lambda player: (-scores.get(player["seat"], 0), player["seat"])
        )[:3]
        role_labels = {
            "werewolf": "狼人", "wolf_beauty": "狼美人", "seer": "预言家",
            "witch": "女巫", "knight": "骑士", "guard": "守卫", "hunter": "猎人",
            "mirror_maiden": "魔镜少女", "hidden_wolf": "觉醒隐狼", "villager": "平民",
        }
        impressions = [{
            "seat": player["seat"],
            "impression": f"{player['seat']}号以{role_labels.get(player.get('role'), '公开身份')}参与了关键轮次，判断和行动给我留下了较深印象。",
        } for player in notable]
        comments = "；".join(f"{item['seat']}号：{item['impression']}" for item in impressions)
        text = (f"我是{seat}号，底牌是{view['self']['role_label']}。{result}。{plan}。"
                f"全局里我发言{speeches}次、投票{votes}次，复盘后最需要改进的是更早说明自己的判断依据。"
                f"本局特别印象是：{comments}")
        return text, impressions

    def _pick_mvp(self, view, targets, key):
        scores = self._performance_scores(view, targets)
        maximum = max(scores.values(), default=0)
        finalists = [seat for seat, score in scores.items() if score == maximum]
        return _stable_choice(sorted(finalists), *key)

    def _performance_scores(self, view, targets):
        scores = {int(target): 0 for target in targets}
        roles = {int(player["seat"]): player.get("role") for player in view.get("players", [])}
        winner = view.get("winner")
        for seat, role in roles.items():
            won = ((role in {"werewolf", "wolf_beauty"}) == (winner == "狼人阵营"))
            if won and seat in scores:
                scores[seat] += 2
        for event in view.get("history", []):
            speaker = event.get("speaker")
            if speaker in scores and event.get("kind") in {"ability", "sheriff"}:
                scores[speaker] += 3
            if event.get("phase") == "post_game_speech":
                text = str(event.get("text") or "")
                for seat in scores:
                    scores[seat] += min(text.count(f"{seat}号"), 2)
        return scores


class CodexFileProvider(ActionProvider):
    """File bridge for a background Codex process.

    A request is written once to ``tasks/<id>.json``.  A background worker may
    create ``responses/<id>.json`` containing one intent.  Until then this
    provider returns ``None`` and the game remains safely paused.
    """

    name = "codex"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.tasks = self.root / "tasks"
        self.responses = self.root / "responses"
        self.tasks.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)

    def request_action(self, visible_state, request):
        raw_id = "|".join(str(value) for value in (
            visible_state["game_id"], visible_state["day"], visible_state["phase"],
            visible_state["self"]["seat"], request["action"], request.get("sequence", 0),
        ))
        task_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
        task_path = self.tasks / f"{task_id}.json"
        response_path = self.responses / f"{task_id}.json"
        if response_path.exists():
            data = json.loads(response_path.read_text(encoding="utf-8"))
            return data
        if not task_path.exists():
            payload = {
                "task_id": task_id,
                "instructions": (
                    "只返回一个符合 request 约束的 JSON 行动，不要修改游戏状态。"
                    + board_rules_prompt(visible_state)
                ),
                "request": request,
                "visible_state": visible_state,
            }
            task_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return None


DEFAULT_CODEX_MODEL = "gpt-5.6-sol"


def _desktop_codex_candidates() -> List[Path]:
    """Codex 桌面端自带的原生 ``codex.exe``。

    优先用它而不是 npm 安装的 ``codex.cmd``：后者会多出
    ``cmd.exe → node → codex.exe`` 一层包装，进程树的收尾更麻烦（见
    :func:`_terminate_process_tree`），早期还踩过包装器初始化失败报 ``os error 5``。
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    if not root.is_dir():
        return []
    try:
        return sorted(root.glob("*/codex.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []


def _terminate_process_tree(process: "subprocess.Popen[str]") -> None:
    """Kill ``process`` **and its descendants**, then release our pipe ends.

    必须是整棵树：即使直接子进程只是一个包装器，它的后代（node / codex.exe）
    依然活着并持有继承来的管道句柄。
    """
    if os.name == "nt":
        # /T 要在直接子进程还活着时执行，否则孙子进程会被漏掉。
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
        try:
            process.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _run_cli_with_timeout(command: List[str], payload: str, timeout: int,
                          env: Dict[str, str], creationflags: int = 0):
    """Run one CLI action call, returning ``(returncode, stdout, stderr)``.

    **不要改回 ``subprocess.run(timeout=...)``**：在 Windows 上它在超时后只杀直接
    子进程，紧接着又调一次 ``communicate()`` 想把管道读干净；此时孙子进程仍然握着
    继承来的管道句柄，那次 drain 会永久阻塞 —— 声明的超时形同虚设，面板会连同请求
    锁一起卡死（2026-09-19 实战踩到：整局冻结 10 分钟以上）。
    超时时本函数会杀掉整棵进程树并抛出 ``subprocess.TimeoutExpired``，绝不再碰管道。
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
        # POSIX 下开新会话，超时才可能整组一起杀（见 _terminate_process_tree）。
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        raise
    return process.returncode, stdout or "", stderr or ""


def _configured_codex_model(codex_home: Path) -> str:
    """Read the user's configured model without requiring Python 3.11 TOML."""
    config_path = codex_home / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_CODEX_MODEL
    match = re.search(r"(?m)^\s*model\s*=\s*['\"]([^'\"]+)['\"]\s*$", text)
    return match.group(1).strip() if match else DEFAULT_CODEX_MODEL


class CodexCLIProvider(ActionProvider):
    """Ask the locally authenticated Codex CLI for one isolated player action."""

    name = "codex_cli"
    batch_actions = False

    def __init__(self, timeout: int = 180, model: Optional[str] = None):
        self.timeout = timeout
        # Codex Desktop and Codex CLI share the ChatGPT login stored in the
        # user's standard Codex home.  Keep the game pinned to that home by
        # default instead of accidentally inheriting a different CODEX_HOME
        # from a shell profile.  AIWOLF_CODEX_HOME remains an explicit escape
        # hatch for a deliberately separate CLI profile.
        configured_home = os.environ.get("AIWOLF_CODEX_HOME")
        self.codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
        # ``--ignore-user-config`` below deliberately isolates the invocation,
        # so always pass an explicit model.  Otherwise ChatGPT-authenticated
        # Codex rejects the request as model=None.
        self.model = model or _configured_codex_model(self.codex_home)
        configured_executable = os.environ.get("CODEX_CLI_PATH")
        candidates = []
        if configured_executable and Path(configured_executable).is_file():
            candidates.append(configured_executable)
        # Prefer the desktop-installed native executable.  The npm wrapper can
        # resolve to an older CLI and, under Windows, may fail to initialize its
        # in-process app-server client with os error 5.
        candidates.extend([
            shutil.which("codex.exe"),
            *[str(path) for path in _desktop_codex_candidates()],
            shutil.which("codex.cmd"),
            shutil.which("codex"),
        ])
        self.executable = next((candidate for candidate in candidates if candidate), None)
        if not self.executable:
            raise ValueError("没有找到 Codex CLI，请先安装并登录 Codex。")

    def request_action(self, visible_state, request):
        action = request["action"]
        schema = action_schema(action)
        payload = json.dumps(
            {"request": request, "visible_state": visible_state},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt = action_prompt(action) + board_rules_prompt(visible_state)
        try:
            with tempfile.TemporaryDirectory(prefix="aiwolf_codex_") as directory:
                root = Path(directory)
                schema_path = root / "action.schema.json"
                output_path = root / "action.json"
                schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                command = [
                    self.executable, "exec",
                    "-c", 'model_provider="aiwolf"',
                    "-c", ('model_providers.aiwolf={name="AIWolf HTTPS",'
                           'base_url="https://chatgpt.com/backend-api/codex",'
                           'wire_api="responses",requires_openai_auth=true,'
                           'supports_websockets=false}'),
                    "--ephemeral", "--ignore-user-config",
                    "--ignore-rules", "--sandbox", "read-only", "--skip-git-repo-check",
                    "--color", "never", "-C", str(root), "--output-schema", str(schema_path),
                    "-o", str(output_path), prompt,
                ]
                if self.model:
                    command[2:2] = ["--model", self.model]
                environment = os.environ.copy()
                # Always select the desktop account's auth store for this
                # provider.  Do not copy or inspect credentials in the repo.
                environment["CODEX_HOME"] = str(self.codex_home)
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    returncode, stdout, stderr = _run_cli_with_timeout(
                        command, payload, self.timeout, environment, creationflags)
                except subprocess.TimeoutExpired as exc:
                    raise ValueError(f"Codex 玩家思考超过 {self.timeout} 秒，请重试。") from exc
                if returncode != 0:
                    raise ValueError(summarize_cli_error(stderr or stdout or "", "Codex 玩家"))
                raw = output_path.read_text(encoding="utf-8") if output_path.exists() else stdout
                intent = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex 玩家没有返回有效行动，请重试。") from exc
        validate_intent(intent, request, "Codex 玩家")
        return intent

    @staticmethod
    def _prompt(action: str) -> str:
        """Kept for backwards compatibility; see ``action_prompt``."""
        return action_prompt(action)

    @staticmethod
    def _validate_intent(intent: Dict[str, Any], request: Dict[str, Any]) -> None:
        """Kept for backwards compatibility; see ``validate_intent``."""
        validate_intent(intent, request, "Codex 玩家")


class CliBackend:
    """One CLI that can answer a single player action as structured JSON.

    The spec is declarative on purpose so argv construction stays testable
    without the CLI being installed: nothing here touches the filesystem.
    """

    def __init__(self, key: str, label: str, executables: Sequence[str],
                 template: Sequence[str], prompt_mode: str,
                 model_flag: Optional[str] = None, default_model: Optional[str] = None,
                 note: str = ""):
        if prompt_mode not in {"stdin", "argv"}:
            raise ValueError(f"{key} 的 prompt_mode 只能是 stdin 或 argv")
        if prompt_mode == "argv" and "{prompt}" not in template:
            raise ValueError(f"{key} 用 argv 传提示词，模板里必须有 {{prompt}} 占位符")
        if prompt_mode == "stdin" and "{prompt}" in template:
            raise ValueError(f"{key} 用 stdin 传提示词，模板里不应出现 {{prompt}}")
        self.key = key
        self.label = label
        self.executables = tuple(executables)
        self.template = tuple(template)
        self.prompt_mode = prompt_mode
        self.model_flag = model_flag
        self.default_model = default_model
        self.note = note

    def resolve_executable(self) -> str:
        for candidate in self.executables:
            found = shutil.which(candidate)
            if found:
                return found
        raise ValueError(
            f"没有找到 {self.label} 的可执行文件（{' / '.join(self.executables)}），"
            "请先安装并完成登录。"
        )

    def build_argv(self, executable: str, model: Optional[str] = None,
                   extra_args: Sequence[str] = (), prompt: str = "") -> list:
        """Expand the template into a full command line."""
        model = model or self.default_model
        argv = []
        for token in self.template:
            if token == "{model}":
                if not model:
                    raise ValueError(f"{self.label} 需要在 --model 里指定模型名称")
                argv.append(model)
            elif token == "{prompt}":
                argv.append(prompt)
            else:
                argv.append(token)
        if self.model_flag and model and "{model}" not in self.template:
            argv = [self.model_flag, model, *argv]
        return [executable, *argv, *extra_args]


CLI_BACKENDS: Dict[str, CliBackend] = {
    "ollama": CliBackend(
        key="ollama", label="Ollama",
        executables=("ollama", "ollama.exe"),
        template=("run", "{model}", "--format", "json"),
        prompt_mode="stdin",
        default_model="qwen3",
        note="本地模型，--format json 强制合法 JSON；局面很长时要选上下文窗口够大的模型。",
    ),
    "claude": CliBackend(
        key="claude", label="Claude Code CLI",
        executables=("claude", "claude.cmd"),
        template=("-p", "--output-format", "json", "--max-turns", "1"),
        prompt_mode="stdin",
        model_flag="--model",
        note="--max-turns 1 限制成单轮，避免它中途去调用工具；"
             "输出是 {result: ...} 信封，会被自动拆开。",
    ),
    "gemini": CliBackend(
        key="gemini", label="Gemini CLI",
        executables=("gemini", "gemini.cmd"),
        template=("-p", "{prompt}"),
        prompt_mode="argv",
        model_flag="--model",
        note="提示词走命令行参数，超长局面可能撞上参数长度上限。",
    ),
}

# ``codex`` uses the same user-facing CLI mode as the other local backends,
# but its authentication and structured-output protocol need the dedicated
# Codex implementation above.  Keep it in the selector alongside the generic
# backends without pretending it has the same argv template.
CLI_BACKEND_CHOICES = ("codex", *sorted(CLI_BACKENDS))


class GenericCLIProvider(ActionProvider):
    """Drive any locally installed CLI that can answer one action as JSON.

    Backends only differ in how they are launched and how they print JSON; the
    output schema, the role prompt and the legality checks are shared with the
    Codex provider.  Every call is a fresh process carrying a single seat's
    visible state, which preserves the property that made ``codex-cli`` worth
    having: no shared conversation, so the eleven seats cannot converge on one
    voice.
    """

    name = "cli"
    batch_actions = False

    def __init__(self, backend: str = "ollama", model: Optional[str] = None,
                 timeout: int = 180, retries: int = 2,
                 extra_args: Optional[Sequence[str]] = None):
        if backend not in CLI_BACKENDS:
            raise ValueError(
                f"未知 CLI 后端: {backend}；可选 {', '.join(sorted(CLI_BACKENDS))}"
            )
        self.backend = CLI_BACKENDS[backend]
        self.model = model
        self.timeout = max(10, int(timeout))
        self.retries = max(0, int(retries))
        self.extra_args = list(extra_args or [])
        self.executable = self.backend.resolve_executable()

    def request_action(self, visible_state, request):
        action = request["action"]
        payload = json.dumps(
            {"request": request, "visible_state": visible_state},
            ensure_ascii=False, separators=(",", ":"),
        )
        prompt = action_prompt(action) + board_rules_prompt(visible_state) + (
            "\n输出结构（必须严格遵守，未使用的字段填 null）：\n"
            + json.dumps(action_schema(action), ensure_ascii=False, separators=(",", ":"))
        )
        full_prompt = f"{prompt}\n\n输入JSON：\n{payload}"
        last_error = "未知错误"
        for attempt in range(self.retries + 1):
            attempt_prompt = full_prompt if attempt == 0 else (
                f"{full_prompt}\n\n注意：上一次的返回不合法（{last_error}）。"
                "这一次只输出一个符合上述结构的 JSON 对象，不要解释、不要 markdown 围栏。"
            )
            argv = self.backend.build_argv(
                self.executable, self.model, self.extra_args,
                prompt=attempt_prompt if self.backend.prompt_mode == "argv" else "",
            )
            raw = self._invoke(
                argv, attempt_prompt if self.backend.prompt_mode == "stdin" else None
            )
            try:
                intent = parse_action_payload(raw)
                validate_intent(intent, request, f"{self.backend.label} 玩家")
                return intent
            except ValueError as exc:
                last_error = str(exc)
        raise ValueError(
            f"{self.backend.label} 连续 {self.retries + 1} 次没有返回合法行动：{last_error}"
        )

    def _invoke(self, argv, stdin_text):
        environment = os.environ.copy()
        # Keep JSON prompts/replies lossless when a Python-based CLI inherits a
        # legacy Windows console encoding (the game payload contains Chinese).
        environment.setdefault("PYTHONUTF8", "1")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            returncode, stdout, stderr = _run_cli_with_timeout(
                argv, stdin_text, self.timeout, environment, creationflags)
        except FileNotFoundError as exc:
            raise ValueError(f"无法启动 {self.backend.label}：{exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"{self.backend.label} 思考超过 {self.timeout} 秒，请重试或换更快的后端。"
            ) from exc
        if returncode != 0:
            raise ValueError(summarize_cli_error(stderr or stdout or "", self.backend.label))
        return stdout


class ProviderFailure(ValueError):
    """A failure that retrying will not fix: bad key, quota, unreachable host."""


def summarize_api_error(status: int, body: str, label: str = "模型") -> str:
    """Turn an OpenAI-compatible error response into one actionable line."""
    detail = (body or "").strip()
    message = detail[-300:]
    try:
        data = json.loads(detail)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])[:300]
        elif isinstance(error, str) and error.strip():
            message = error[:300]
        elif data.get("message"):
            message = str(data["message"])[:300]
    lowered = f"{status} {detail}".lower()
    if status in {401, 403} or any(token in lowered for token in _AUTH_HINTS):
        return f"{label}的 API Key 无效或已过期（HTTP {status}），请检查 config/ai_config.json。原始提示：{message}"
    if status == 429 or any(token in lowered for token in _QUOTA_HINTS):
        return f"{label}的额度或频率已用尽（HTTP {status}），请稍后重试或换一个模型。原始提示：{message}"
    return f"{label}调用失败（HTTP {status}）：{message}"


def _clean(value: Any) -> str:
    """Return the value unless it still looks like a config template."""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    placeholders = ("your-api-key-here", "your-api-endpoint", "your_base_url",
                    "sk-xxx", "changeme", "your-endpoint")
    if any(token in lowered for token in placeholders):
        return ""
    return text


def load_api_profile(name: Optional[str] = None, config_path: Optional[Path] = None,
                     base_url: Optional[str] = None, api_key: Optional[str] = None,
                     model: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one OpenAI-compatible endpoint.

    Reads ``config/ai_config.json`` — the same file the original API simulator
    used, so nothing has to be re-entered.  Explicit arguments and the
    ``AIWOLF_BASE_URL`` / ``AIWOLF_API_KEY`` / ``AIWOLF_MODEL`` environment
    variables all take precedence, so a key can be handed over without editing
    the file at all.
    """
    path = Path(config_path) if config_path else Path(__file__).resolve().parents[1] / "config" / "ai_config.json"
    players: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 不是合法的 JSON：{exc}") from exc
        players = (data or {}).get("ai_players") or {}

    explicit_base = _clean(base_url) or _clean(os.environ.get("AIWOLF_BASE_URL"))
    explicit_key = _clean(api_key) or _clean(os.environ.get("AIWOLF_API_KEY"))
    explicit_model = _clean(model) or _clean(os.environ.get("AIWOLF_MODEL"))

    entry_name, entry = "", {}
    if name:
        matches = [(key, value) for key, value in players.items()
                   if key.strip().lower() == str(name).strip().lower()]
        if not matches:
            available = ", ".join(players) or "（文件里没有 ai_players）"
            raise ValueError(f"ai_config.json 里没有名为 {name} 的模型；可选 {available}")
        entry_name, entry = matches[0]
    else:
        ready = [(key, value) for key, value in players.items()
                 if _clean(value.get("api_key")) and _clean(value.get("baseurl"))]
        if ready:
            entry_name, entry = ready[0]
        elif players and not (explicit_base and explicit_key and explicit_model):
            # Only borrow the first entry when it can still contribute something;
            # otherwise everything came from the command line and naming the
            # profile after an unrelated file entry would just be misleading.
            entry_name, entry = next(iter(players.items()))

    resolved_base = explicit_base or _clean(entry.get("baseurl"))
    resolved_key = explicit_key or _clean(entry.get("api_key"))
    resolved_model = explicit_model or _clean(entry.get("model"))
    if not resolved_base or not resolved_key:
        raise ValueError(
            "还没有可用的 API 配置。请把 config/ai_config.json 里某个条目的 baseurl 和 api_key "
            "填成你自己的，或者用 --api-base-url / --api-key / --model 直接指定。"
        )
    if not resolved_model:
        raise ValueError("缺少模型名称：请填写配置文件里的 model 字段，或用 --model 指定。")
    try:
        timeout = int(entry.get("timeout") or 0)
    except (TypeError, ValueError):
        timeout = 0
    return {"name": entry_name or "自定义", "baseurl": resolved_base,
            "api_key": resolved_key, "model": resolved_model, "timeout": timeout}


class ApiProvider(ActionProvider):
    """OpenAI-compatible chat completions, one fresh request per seat action.

    Deliberately plain ``urllib`` rather than the ``openai`` package so the
    panel gains no import-time dependency; the wire format is the one every
    compatible vendor implements.  Like ``GenericCLIProvider`` it shares the
    schema, the role prompt and the legality checks, and each call carries a
    single seat's visible state so the eleven seats stay independent.
    """

    name = "api"
    batch_actions = False

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60,
                 retries: int = 2, response_format: str = "json_object",
                 label: str = "模型"):
        self.base_url = str(base_url).rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.timeout = max(5, int(timeout))
        self.retries = max(0, int(retries))
        self.response_format = response_format or "json_object"
        self.label = label

    def request_action(self, visible_state, request):
        action = request["action"]
        schema = action_schema(action)
        system = action_prompt(action) + board_rules_prompt(visible_state) + (
            "\n输出结构（必须严格遵守，未使用的字段填 null）：\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
        payload = json.dumps({"request": request, "visible_state": visible_state},
                             ensure_ascii=False, separators=(",", ":"))
        last_error = "未知错误"
        for attempt in range(self.retries + 1):
            user = payload if attempt == 0 else (
                f"{payload}\n\n注意：上一次的返回不合法（{last_error}）。"
                "这一次只输出一个符合上述结构的 JSON 对象，不要解释、不要 markdown 围栏。"
            )
            body = self._build_body(system, user, action)
            status, text = self._post(body)
            if status == 400 and "response_format" in text and self.response_format != "none":
                self.response_format = "none"
                status, text = self._post(self._build_body(system, user, action))
            if status != 200:
                raise ProviderFailure(
                    summarize_api_error(status, text, f"{self.label}")
                )
            try:
                content = self._extract_content(text)
                intent = parse_action_payload(content)
                validate_intent(intent, request, f"{self.label}")
                return intent
            except ProviderFailure:
                raise
            except ValueError as exc:
                last_error = str(exc)
        raise ValueError(
            f"{self.label}连续 {self.retries + 1} 次没有返回合法行动：{last_error}"
        )

    def _build_body(self, system: str, user: str, action: str) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if self.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.response_format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "wolf_action", "strict": True,
                                "schema": action_schema(action)},
            }
        return body

    def _post(self, body: Dict[str, Any]):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise ProviderFailure(f"连不上 {self.base_url}：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderFailure(
                f"{self.label}响应超过 {self.timeout} 秒，请重试或调大超时。") from exc

    @staticmethod
    def _extract_content(text: str) -> str:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"接口返回的不是 JSON：{text[:200]}") from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else error
            raise ProviderFailure(f"接口返回错误：{str(message)[:200]}")
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise ValueError(f"接口没有返回 choices：{text[:200]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict))
        if not str(content or "").strip():
            raise ValueError("接口返回了空内容，请重试。")
        return str(content)


def split_cli_args(text: str) -> list:
    """Split extra CLI arguments without mangling Windows paths.

    ``shlex.split`` runs in POSIX mode by default, where a backslash is an
    escape character, so ``--received-dir=C:\\temp`` would silently lose its
    backslashes.  On Windows we therefore only honour double quotes.
    """
    if os.name != "nt":
        return shlex.split(text)
    return [token.strip('"') for token in re.findall(r'"[^"]*"|\S+', text)]


def create_provider(name: str, bridge_root: Optional[Path] = None,
                    model: Optional[str] = None, cli: Optional[str] = None,
                    cli_args: Optional[str] = None, timeout: Optional[int] = None,
                    retries: Optional[int] = None, api_model: Optional[str] = None,
                    api_key: Optional[str] = None, api_base_url: Optional[str] = None,
                    api_format: Optional[str] = None,
                    api_config: Optional[Path] = None) -> ActionProvider:
    if name == "codex":
        if bridge_root is None:
            raise ValueError("Codex Provider 需要本地桥接目录")
        return CodexFileProvider(bridge_root)
    if name == "cli":
        selected_cli = cli or "codex"
        if selected_cli == "codex":
            return CodexCLIProvider(model=model, timeout=timeout or 180)
        return GenericCLIProvider(
            backend=selected_cli,
            model=model,
            timeout=timeout or 180,
            retries=2 if retries is None else retries,
            extra_args=split_cli_args(cli_args) if cli_args else None,
        )
    # Keep the old constructor spelling for callers that import this module
    # directly.  The panel no longer exposes it as a separate mode.
    if name == "codex-cli":
        return CodexCLIProvider(model=model, timeout=timeout or 180)
    if name == "api":
        profile = load_api_profile(api_model, api_config,
                                   base_url=api_base_url, api_key=api_key, model=model)
        return ApiProvider(
            base_url=profile["baseurl"],
            api_key=profile["api_key"],
            model=profile["model"],
            timeout=timeout or profile["timeout"] or 60,
            retries=2 if retries is None else retries,
            response_format=api_format or "json_object",
            label=f"{profile['name']} 模型",
        )
    raise ValueError(f"未知 AI Provider: {name}")
