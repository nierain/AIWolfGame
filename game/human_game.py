"""Authoritative state machine for one human and eleven computer players.

The module is deliberately independent from HTTP and model SDKs.  It owns all
rule validation and exposes a player-scoped view to decision providers.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from .ai_providers import ActionProvider
from .ai_profiles import (
    HUMAN_AVATAR,
    HUMAN_DISPLAY_NAME,
    PROFILE_TEMPLATES,
    assign_profiles,
    compact_memory,
)


ROLE_LABELS = {
    "werewolf": "狼人", "wolf_beauty": "狼美人", "seer": "预言家",
    "witch": "女巫", "knight": "骑士", "guard": "守卫", "villager": "平民",
    "hunter": "猎人", "mirror_maiden": "魔镜少女", "hidden_wolf": "觉醒隐狼",
}
WOLF_ROLES = {"werewolf", "wolf_beauty", "hidden_wolf"}
GOD_ROLES = {"seer", "witch", "knight", "guard", "hunter", "mirror_maiden"}
ROLE_DECK = (["werewolf"] * 3 + ["wolf_beauty", "seer", "witch", "knight", "guard"]
             + ["villager"] * 4)
# 镜隐迷踪：三小狼 + 觉醒隐狼 vs 魔镜少女 + 守卫 + 女巫 + 猎人 + 四平民
MIRROR_DECK = (["werewolf"] * 3 + ["hidden_wolf", "mirror_maiden", "guard", "witch", "hunter"]
               + ["villager"] * 4)
BOARD_DECKS = {"classic": ROLE_DECK, "mirror": MIRROR_DECK}
BOARD_NAMES = {
    "classic": "预女骑守 + 狼美人",
    "mirror": "镜隐迷踪",
}
# 隐狼学到的身份 → 它对魔镜少女显现的身份（学什么显示什么，学狼人显示"狼人"）
HIDDEN_WOLF_LEARNABLE = {
    "witch": "witch", "seer": "seer", "guard": "guard", "hunter": "hunter",
    "villager": "villager", "werewolf": "werewolf", "wolf_beauty": "werewolf",
    "hidden_wolf": "werewolf",
}


def setup_state() -> Dict[str, Any]:
    return {
        "schema": 2,
        "phase": "setup",
        "day": 0,
        "history": [],
        "players": [],
        "pending": None,
        "winner": None,
        "setup": {"human_seat": 0, "debug_role": "random"},
    }


def _seeded_rng(seed: Optional[int]) -> random.Random:
    return random.Random(seed if seed is not None else random.SystemRandom().randrange(2**63))


def create_game(human_seat: int = 0, debug_role: str = "random",
                seed: Optional[int] = None, wolf_self_kill: bool = True,
                profile_memories: Optional[Dict[str, Any]] = None,
                board: str = "classic") -> Dict[str, Any]:
    if human_seat not in range(0, 13):
        raise ValueError("真人座位必须为0（随机）或1到12")
    if board not in BOARD_DECKS:
        raise ValueError("未知板子")
    if debug_role not in {*ROLE_LABELS, "random"}:
        raise ValueError("调试身份无效")
    rng = _seeded_rng(seed)
    chosen_seat = human_seat or rng.randint(1, 12)
    deck = list(BOARD_DECKS[board])
    if debug_role != "random":
        deck.remove(debug_role)
        rng.shuffle(deck)
        roles = {chosen_seat: debug_role}
        other_seats = [seat for seat in range(1, 13) if seat != chosen_seat]
        roles.update(dict(zip(other_seats, deck)))
    else:
        rng.shuffle(deck)
        roles = dict(zip(range(1, 13), deck))
    game_id = uuid.uuid4().hex
    selected_profile_ids = [profile["id"] for profile in rng.sample(PROFILE_TEMPLATES, 11)]
    ai_profiles = assign_profiles(chosen_seat, profile_memories, selected_profile_ids)
    state: Dict[str, Any] = {
        "schema": 2, "game_id": game_id, "seed": seed, "day": 0,
        "phase": "init", "event_seq": 0, "action_seq": 0, "history": [],
        "roles": {str(seat): role for seat, role in roles.items()},
        "players": {str(seat): {"seat": seat, "alive": True, "revealed_role": None,
                                  "is_human": seat == chosen_seat}
                    for seat in range(1, 13)},
        "human_seat": chosen_seat, "winner": None, "pending": None,
        "phase_data": {}, "night": {}, "public_votes": {}, "last_exiled": None,
        "last_duel": None,
        "postgame_impressions": {}, "mvp_votes": {}, "mvp_result": {},
        "sheriff_candidates": [],
        "sheriff_election_players": [], "vote_summary": {},
        "sheriff": None, "sheriff_badge": True, "badge_transfer_required": None,
        "sheriff_election_delayed": False,
        "abilities": {
            "witch_medicine": True, "witch_poison": True, "guard_last": None,
            "knight_used": False, "seer_checks": {}, "beauty_charm": {},
            # 镜隐迷踪专用
            "hidden_wolf_learned": {},   # {seat: learned_role}
            "hidden_wolf_poison": True,  # 隐狼学到女巫后继承的"救不活的毒"
            "hidden_wolf_checks": {},    # 隐狼学到预言家后的查验记录
            "hidden_wolf_guard_last": None,  # 隐狼学到守卫后的连守记录
            "hunter_shots": {},          # {seat: True 已开枪}
        },
        "board": board,
        "wolf_chat": [],
        "ai_profiles": ai_profiles,
        "ai_memories": {
            str(seat): {"profile_id": ai_profiles[str(seat)]["id"],
                        "name": ai_profiles[str(seat)]["name"],
                        "avatar": ai_profiles[str(seat)]["avatar"],
                        "personality": ai_profiles[str(seat)]["personality"],
                        "speech_style": ai_profiles[str(seat)]["speech_style"],
                        "risk_style": ai_profiles[str(seat)]["risk_style"],
                        "long_term_summary": ai_profiles[str(seat)]["long_term_summary"],
                        "recent_game_memories": deepcopy(ai_profiles[str(seat)]["recent_game_memories"]),
                        "own_speeches": [], "votes": [],
                        "beliefs": {"suspects": [], "trusted": []},
                        "strategy": "根据公开发言和票型逐轮修正判断", "notes": []}
            for seat in range(1, 13) if seat != chosen_seat
        },
        "rules": {
            "board": BOARD_NAMES[board], "win_condition": "屠边",
            "sheriff_vote_weight": 1.5, "guard_cannot_repeat": True,
            "guard_save_conflict_kills": True,
            "wolf_can_self_kill": bool(wolf_self_kill),
        },
    }
    _add_event(state, "system", "新游戏开始：天黑请闭眼。")
    _start_night(state)
    return state


def _role(state: Dict[str, Any], seat: int) -> str:
    return state["roles"][str(seat)]


def _player(state: Dict[str, Any], seat: int) -> Dict[str, Any]:
    return state["players"][str(seat)]


def _alive(state: Dict[str, Any]) -> List[int]:
    return [seat for seat in range(1, 13) if _player(state, seat)["alive"]]


def _alive_role(state: Dict[str, Any], role: str) -> List[int]:
    return [seat for seat in _alive(state) if _role(state, seat) == role]


def _add_event(state: Dict[str, Any], kind: str, text: str, speaker: Optional[int] = None,
               phase: Optional[str] = None, **extra: Any) -> None:
    state["event_seq"] += 1
    event = {"id": state["event_seq"], "day": state["day"], "phase": phase or state["phase"],
             "kind": kind, "speaker": speaker, "text": text}
    event.update(extra)
    state["history"].append(event)


def _remember(state: Dict[str, Any], seat: int, category: str, value: Any) -> None:
    memory = state["ai_memories"].get(str(seat))
    if memory is None:
        return
    if category == "speech":
        memory["own_speeches"].append({"day": state["day"], "text": value})
    elif category == "vote":
        memory["votes"].append({"day": state["day"], "target": value, "phase": state["phase"]})
        if value is not None and value not in memory["beliefs"]["suspects"]:
            memory["beliefs"]["suspects"].append(value)
            memory["beliefs"]["suspects"] = memory["beliefs"]["suspects"][-4:]
    else:
        memory["notes"].append(value)


def _request(state: Dict[str, Any], actor: int, action: str, allowed: Iterable[int] = (),
             prompt: str = "", **options: Any) -> None:
    state["action_seq"] += 1
    state["pending"] = {
        "actor": actor, "action": action, "allowed_targets": list(allowed),
        "prompt": prompt, "sequence": state["action_seq"], "waiting_external": False,
        **options,
    }


def _start_queue(state: Dict[str, Any], phase: str, action: str, actors: Iterable[int],
                 *, allowed: Optional[Iterable[int]] = None, prompt: str = "",
                 simultaneous: bool = False, include_dead: bool = False) -> None:
    actors = [actor for actor in actors if include_dead or _player(state, actor)["alive"]]
    if simultaneous and state["human_seat"] in actors:
        actors = [state["human_seat"]] + [actor for actor in actors if actor != state["human_seat"]]
    state["phase"] = phase
    state["phase_data"] = {"queue": actors, "index": 0, "action": action,
                           "fixed_allowed": list(allowed) if allowed is not None else None,
                           "prompt": prompt, "simultaneous": simultaneous,
                           "published": False, "include_dead": include_dead}
    if actors:
        _queue_request(state)
    else:
        _queue_complete(state)


def _queue_actor(state: Dict[str, Any]) -> Optional[int]:
    data = state["phase_data"]
    while data["index"] < len(data["queue"]):
        actor = data["queue"][data["index"]]
        if (data.get("include_dead") or _player(state, actor)["alive"]
                or data["action"] == "sheriff_transfer"):
            return actor
        data["index"] += 1
    return None


def _targets_for(state: Dict[str, Any], actor: int, action: str) -> List[int]:
    alive = _alive(state)
    if action == "wolf_chat":
        return [seat for seat in alive if _role(state, seat) not in WOLF_ROLES]
    if action == "wolf_kill":
        if state["rules"].get("wolf_can_self_kill"):
            return list(alive)
        return [seat for seat in alive if _role(state, seat) not in WOLF_ROLES]
    if action == "charm":
        return [seat for seat in alive if seat != actor and _role(state, seat) not in WOLF_ROLES]
    if action == "divine":
        return [seat for seat in alive if seat != actor]
    if action == "mirror_peek":
        return [seat for seat in alive if seat != actor]
    if action == "hidden_learn":
        return [seat for seat in alive if seat != actor]
    if action == "hidden_blade":
        # 隐狼带刀：不能刀自己；学狼人可双刀（两个不同目标）
        return [seat for seat in alive if seat != actor]
    if action == "hidden_skill":
        # 继承技能：查验/守护/毒，目标都不含自己（守可以含，但统一排自己更安全）
        return [seat for seat in alive if seat != actor]
    if action == "guard":
        targets = list(alive)
        if state["rules"]["guard_cannot_repeat"] and state["abilities"]["guard_last"] in targets:
            targets.remove(state["abilities"]["guard_last"])
        return targets
    if action == "witch_poison":
        return [seat for seat in alive if seat != actor]
    if action in {"vote", "sheriff_recommend"}:
        fixed = state["phase_data"].get("fixed_allowed")
        return list(fixed) if fixed is not None else [seat for seat in alive if seat != actor]
    if action == "knight_decide":
        return [seat for seat in alive if seat != actor]
    if action == "sheriff_transfer":
        return [seat for seat in alive if seat != actor]
    if action == "mvp_vote":
        return list(range(1, 13))
    return []


def _queue_request(state: Dict[str, Any]) -> None:
    actor = _queue_actor(state)
    if actor is None:
        _queue_complete(state)
        return
    data = state["phase_data"]
    action = data["action"]
    allowed = data.get("fixed_allowed")
    if allowed is None:
        allowed = _targets_for(state, actor, action)
    prompt = data.get("prompt", "")
    if action == "witch_save":
        victim = state["night"].get("wolf_target")
        prompt = f"今晚{victim}号被狼人袭击，是否使用解药？" if victim else "今晚无人被狼人袭击。"
    _request(state, actor, action, allowed, prompt)


def _advance_queue(state: Dict[str, Any]) -> None:
    state["phase_data"]["index"] += 1
    _queue_request(state)


def _start_night(state: Dict[str, Any]) -> None:
    state["phase"] = "night_wolf_chat"
    state["night"] = {"wolf_votes": {}, "wolf_target": None, "guard": None,
                      "saved": False, "poison": None, "deaths": [],
                      "wolf_targets": [],  # mirror 板子双刀用（小狼刀 + 隐狼刀）
                      "hidden_poison": None, "hidden_guard": None}
    wolves = [seat for seat in _alive(state) if _role(state, seat) in WOLF_ROLES]
    if state["human_seat"] in wolves:
        wolves = [state["human_seat"]]
    _add_event(state, "system", f"第{state['day'] + 1}夜开始。", phase="night")
    if state.get("board") == "mirror":
        # 镜隐迷踪：第 1 夜先让隐狼学习，且隐狼不参与小狼狼聊/刀口投票
        hidden = _alive_role(state, "hidden_wolf")
        _start_hidden_wolf_learn(state)
        return
    _start_queue(state, "night_wolf_chat", "wolf_chat", wolves,
                 prompt=("你是狼队指挥，请独立安排刀口、悍跳、冲锋或倒钩战术。电脑狼会执行你的安排。"
                         if state["human_seat"] in wolves
                         else "与狼队讨论刀口、悍跳、冲锋或倒钩安排。"))


def _start_hidden_wolf_learn(state: Dict[str, Any]) -> None:
    """镜隐迷踪：第 1 夜隐狼学习一名玩家（终身固定，不能学自己）。"""
    hidden = _alive_role(state, "hidden_wolf")
    if hidden and not any(seat in state["abilities"]["hidden_wolf_learned"] for seat in hidden):
        _start_queue(state, "night_hidden_learn", "hidden_learn", hidden,
                     prompt="选择一名玩家学习，获得其身份与技能（不能学自己，终身固定）。")
    else:
        _start_mirror_wolf_chat(state)


def _start_mirror_wolf_chat(state: Dict[str, Any]) -> None:
    """镜隐迷踪：小狼聊刀口（隐狼不互通、不参与）。"""
    wolves = [seat for seat in _alive(state) if _role(state, seat) in {"werewolf"}]
    if state["human_seat"] in wolves:
        wolves = [state["human_seat"]]
    _start_queue(state, "night_wolf_chat", "wolf_chat", wolves,
                 prompt=("你是狼队指挥，请独立安排刀口、悍跳、冲锋或倒钩战术。电脑狼会执行你的安排。"
                         if state["human_seat"] in wolves
                         else "与狼队讨论刀口、悍跳、冲锋或倒钩安排。"))


def _mirror_hidden_has_blade(state: Dict[str, Any]) -> bool:
    """镜隐迷踪：入夜时场上没有小狼 → 隐狼带刀。"""
    return not any(_role(state, seat) == "werewolf" for seat in _alive(state))


def _mirror_queue_complete(state: Dict[str, Any], phase: str) -> None:
    """镜隐迷踪的夜间顺序链。"""
    if phase == "night_hidden_learn":
        _start_mirror_wolf_chat(state)
    elif phase == "night_wolf_chat":
        # 小狼聊完 → 小狼投票定刀口
        wolves = [seat for seat in _alive(state) if _role(state, seat) in {"werewolf"}]
        if state["human_seat"] in wolves:
            wolves = [state["human_seat"]]
        _start_queue(state, "night_wolf_vote", "wolf_kill", wolves,
                     prompt="选择今晚的狼人击杀目标。")
    elif phase == "night_wolf_vote":
        votes = state["night"]["wolf_votes"]
        state["night"]["wolf_target"] = _plurality(votes, state, "wolf-kill")
        _mirror_start_hidden_blade(state)
    elif phase == "night_hidden_blade":
        # 隐狼带刀后决定自己的刀口
        _mirror_start_maiden(state)
    elif phase == "night_maiden":
        _mirror_start_hidden_skill(state)
    elif phase == "night_hidden_skill":
        _start_queue(state, "night_guard", "guard", _alive_role(state, "guard"),
                     prompt="选择今晚的守护目标，不能连续两晚守同一人。")
    elif phase == "night_guard":
        _start_queue(state, "night_witch_save", "witch_save", _alive_role(state, "witch"),
                     prompt="今晚狼人袭击了目标，是否使用解药？")
    elif phase == "night_witch_save":
        _start_witch_poison(state)
    elif phase == "night_witch_poison":
        _resolve_night(state)


def _mirror_start_hidden_blade(state: Dict[str, Any]) -> None:
    """镜隐迷踪：入夜时小狼全灭 → 隐狼带刀，决定自己的刀口。"""
    hidden = _alive_role(state, "hidden_wolf")
    if not hidden:
        _mirror_start_maiden(state)
        return
    if not _mirror_hidden_has_blade(state):
        _mirror_start_maiden(state)
        return
    learned = state["abilities"]["hidden_wolf_learned"].get(str(hidden[0]))
    double = learned in {"werewolf"}  # 学狼人才能双刀
    prompt = ("小狼已全灭，你已获得狼刀。你学的是狼人，今晚可连刀两名不同玩家。"
              if double else "小狼已全灭，你已获得狼刀，今晚可刀一名玩家。")
    _start_queue(state, "night_hidden_blade", "hidden_blade", hidden, prompt=prompt)


def _mirror_start_maiden(state: Dict[str, Any]) -> None:
    _start_queue(state, "night_maiden", "mirror_peek", _alive_role(state, "mirror_maiden"),
                 prompt="选择一名玩家查验，将得知其具体身份。")


def _mirror_start_hidden_skill(state: Dict[str, Any]) -> None:
    """镜隐迷踪：隐狼使用继承技能（预言/守护/毒）。

    规则：第 1 晚学到的技能，第 1 晚都不能使用（本局第 1 夜 day==0），从第 2 夜起可用。
    """
    # 第 1 夜（day==0）隐狼刚完成学习，所有继承技能本夜不可用
    if state["day"] == 0:
        _start_queue(state, "night_guard", "guard", _alive_role(state, "guard"),
                     prompt="选择今晚的守护目标，不能连续两晚守同一人。")
        return
    hidden = _alive_role(state, "hidden_wolf")
    for seat in hidden:
        learned = state["abilities"]["hidden_wolf_learned"].get(str(seat))
        if learned == "seer":
            _start_queue(state, "night_hidden_skill", "hidden_skill", [seat],
                         prompt="你学的是预言家，可查验一名玩家。")
            state["phase_data"]["skill"] = "seer"
            return
        if learned == "guard":
            _start_queue(state, "night_hidden_skill", "hidden_skill", [seat],
                         prompt="你学的是守卫，可守护一名玩家（可挡毒）。")
            state["phase_data"]["skill"] = "guard"
            return
        if learned == "witch" and state["abilities"]["hidden_wolf_poison"]:
            _start_queue(state, "night_hidden_skill", "hidden_skill", [seat],
                         prompt="你学的是女巫，可用毒药（被你的毒击杀者女巫也救不活）。")
            state["phase_data"]["skill"] = "poison"
            return
    # 没有可用的继承技能 → 直接进守卫
    _start_queue(state, "night_guard", "guard", _alive_role(state, "guard"),
                 prompt="选择今晚的守护目标，不能连续两晚守同一人。")


def _publish_simultaneous_result(state: Dict[str, Any]) -> None:
    data = state.get("phase_data", {})
    if not data.get("simultaneous") or data.get("published"):
        return
    phase = state["phase"]
    if phase == "sheriff_campaign":
        decisions = data.get("campaign_decisions", {})
        joined = sorted(int(seat) for seat, value in decisions.items() if value)
        stayed = sorted(int(seat) for seat, value in decisions.items() if not value)
        parts = []
        if joined:
            parts.append("上警：" + "、".join(f"{seat}号" for seat in joined))
        if stayed:
            parts.append("不上警：" + "、".join(f"{seat}号" for seat in stayed))
        _add_event(state, "sheriff", "上警结果同时公布——" + "；".join(parts) + "。", phase="sheriff")
    elif data.get("action") == "withdraw":
        withdrawals = data.get("withdrawals", {})
        left = sorted(int(seat) for seat, value in withdrawals.items() if value)
        stayed = sorted(int(seat) for seat, value in withdrawals.items() if not value)
        parts = []
        if left:
            parts.append("退水：" + "、".join(f"{seat}号" for seat in left))
        if stayed:
            parts.append("继续竞选：" + "、".join(f"{seat}号" for seat in stayed))
        state["sheriff_candidates"] = list(data.get("remaining", []))
        _add_event(state, "sheriff", "退水结果同时公布——" + "；".join(parts) + "。", phase="sheriff")
    elif data.get("action") == "vote":
        votes = data.get("votes", {})
        records = [
            f"{voter}→{target}号" if target is not None else f"{voter}→弃票"
            for voter, target in sorted(votes.items(), key=lambda item: int(item[0]))
        ]
        sheriff_vote = phase.startswith("sheriff")
        counts: Dict[int, float] = {}
        abstain = 0
        for voter, target in votes.items():
            if target is None:
                abstain += 1
                continue
            weight = 1.0
            if not sheriff_vote and int(voter) == state.get("sheriff"):
                weight = state["rules"]["sheriff_vote_weight"]
            counts[target] = counts.get(target, 0.0) + weight
        total_parts = [f"{target}号{count:g}票" for target, count in sorted(counts.items())]
        if abstain:
            total_parts.append(f"弃票{abstain}票")
        summary = "；汇总：" + "、".join(total_parts) if total_parts else "；汇总：无人得票"
        _add_event(state, "vote", "投票同时公布——" + "；".join(records) + summary + "。")
        state["public_votes"] = deepcopy(votes)
        state["vote_summary"] = {
            "phase": phase,
            "counts": {str(target): count for target, count in counts.items()},
            "abstain": abstain,
        }
    elif data.get("action") == "mvp_vote":
        votes = data.get("votes", {})
        counts: Dict[int, int] = {}
        records = []
        for voter, ballot in sorted(votes.items(), key=lambda item: int(item[0])):
            target = int(ballot["target"])
            reason = str(ballot["reason"])
            counts[target] = counts.get(target, 0) + 1
            records.append({"voter": int(voter), "target": target, "reason": reason})
            _add_event(
                state, "mvp_vote", f"{voter}号投给{target}号：{reason}",
                speaker=int(voter), phase="mvp_vote",
            )
        maximum = max(counts.values(), default=0)
        winners = sorted(seat for seat, count in counts.items() if count == maximum)
        state["mvp_votes"] = {str(item["voter"]): {
            "target": item["target"], "reason": item["reason"]
        } for item in records}
        state["mvp_result"] = {
            "counts": {str(seat): count for seat, count in sorted(counts.items())},
            "winners": winners,
            "votes": records,
        }
        names = "、".join(f"{seat}号" for seat in winners) or "无人"
        _add_event(
            state, "system", f"MVP票选结束：{names}以{maximum}票当选。",
            phase="mvp_vote",
        )
    data["published"] = True


def _queue_complete(state: Dict[str, Any]) -> None:
    phase = state["phase"]
    _publish_simultaneous_result(state)
    state["pending"] = None
    # mirror 板子只有夜间链不同；白天阶段（警长竞选/发言/退水/投票/复盘等）
    # 必须走回原链，否则队列耗尽后 pending=None 直接死锁。
    if state.get("board") == "mirror" and phase in {
        "night_hidden_learn", "night_wolf_chat", "night_wolf_vote",
        "night_hidden_blade", "night_maiden", "night_hidden_skill",
        "night_guard", "night_witch_save", "night_witch_poison",
    }:
        _mirror_queue_complete(state, phase)
        return
    if phase == "night_wolf_chat":
        wolves = [seat for seat in _alive(state) if _role(state, seat) in WOLF_ROLES]
        if state["human_seat"] in wolves:
            wolves = [state["human_seat"]]
        _start_queue(state, "night_wolf_vote", "wolf_kill", wolves,
                     prompt=("你负责决定狼队今晚的最终刀口。"
                             if state["human_seat"] in wolves
                             else "选择今晚的狼人击杀目标。"))
    elif phase == "night_wolf_vote":
        votes = state["night"]["wolf_votes"]
        state["night"]["wolf_target"] = _plurality(votes, state, "wolf-kill")
        beauties = _alive_role(state, "wolf_beauty")
        _start_queue(state, "night_beauty", "charm", beauties,
                     prompt="选择今晚的魅惑目标。")
    elif phase == "night_beauty":
        _start_queue(state, "night_guard", "guard", _alive_role(state, "guard"),
                     prompt="选择今晚的守护目标，不能连续两晚守同一人。")
    elif phase == "night_guard":
        _start_queue(state, "night_seer", "divine", _alive_role(state, "seer"),
                     prompt="选择一名存活玩家查验。")
    elif phase == "night_seer":
        witches = _alive_role(state, "witch")
        if witches and state["abilities"]["witch_medicine"]:
            _start_queue(state, "night_witch_save", "witch_save", witches)
        else:
            _start_witch_poison(state)
    elif phase == "night_witch_save":
        _start_witch_poison(state)
    elif phase == "night_witch_poison":
        _resolve_night(state)
    elif phase == "sheriff_campaign":
        candidates = state["phase_data"].get("candidates", [])
        _start_sheriff_speeches(state, candidates)
    elif phase == "sheriff_speech":
        candidates = state["phase_data"].get("candidates", [])
        _start_withdraw(state, candidates)
    elif phase == "sheriff_withdraw":
        candidates = state["phase_data"].get("remaining", [])
        _start_sheriff_vote(state, candidates)
    elif phase == "sheriff_vote":
        _finish_sheriff_vote(state, revote=False)
    elif phase == "sheriff_pk_speech":
        tied = state["phase_data"].get("tied", [])
        voters = [seat for seat in _alive(state)
                  if seat not in state.get("sheriff_election_players", [])]
        _start_queue(state, "sheriff_pk_vote", "vote", voters, allowed=tied,
                     prompt="警长竞选PK复投，请选择候选人。", simultaneous=True)
        state["phase_data"]["votes"] = {}
        state["phase_data"]["tied"] = tied
        state["public_votes"] = {}
        state["vote_summary"] = {}
    elif phase == "sheriff_pk_vote":
        _finish_sheriff_vote(state, revote=True)
    elif phase == "day_speech":
        sheriff = state.get("sheriff")
        if sheriff in _alive(state):
            _start_queue(state, "sheriff_recommend", "sheriff_recommend", [sheriff],
                         prompt="警长请选择今天建议放逐的目标。")
        else:
            _start_day_vote(state)
    elif phase == "sheriff_recommend":
        _start_day_vote(state)
    elif phase == "day_vote":
        _finish_day_vote(state, revote=False)
    elif phase == "day_pk_speech":
        tied = state["phase_data"].get("tied", [])
        voters = [seat for seat in _alive(state) if seat not in tied]
        _start_queue(state, "day_revote", "vote", voters, allowed=tied,
                     prompt="放逐PK复投，请在平票玩家中选择。", simultaneous=True)
        state["phase_data"]["votes"] = {}
        state["phase_data"]["tied"] = tied
        state["public_votes"] = {}
        state["vote_summary"] = {}
    elif phase == "day_revote":
        _finish_day_vote(state, revote=True)
    elif phase == "post_game_speech":
        _start_mvp_vote(state)
    elif phase == "mvp_vote":
        state["phase"] = "game_over"
        state["phase_data"] = {}
        _add_event(state, "system", "赛后复盘与MVP票选结束，感谢所有玩家。", phase="game_over")
    else:
        raise RuntimeError(f"未处理的阶段结束: {phase}")


def _start_witch_poison(state: Dict[str, Any]) -> None:
    witches = _alive_role(state, "witch")
    if witches and state["abilities"]["witch_poison"]:
        _start_queue(state, "night_witch_poison", "witch_poison", witches,
                     prompt="选择是否使用毒药；可以不使用。")
    else:
        _resolve_night(state)


def _resolve_night(state: Dict[str, Any]) -> None:
    night = state["night"]
    guard = night.get("guard")
    saved = night.get("saved", False)
    deaths: List[int] = []

    if state.get("board") == "mirror":
        # 双刀：小狼刀 + 隐狼刀（可能只有一把）
        targets = []
        if night.get("wolf_target"):
            targets.append(night["wolf_target"])
        for t in night.get("wolf_targets", []):
            if t and t not in targets:
                targets.append(t)
        for victim in targets:
            protected = victim == guard or saved
            if victim == guard and saved and state["rules"]["guard_save_conflict_kills"]:
                protected = False
            if not protected:
                if victim not in deaths:
                    deaths.append(victim)
        # 隐狼继承女巫的毒（救不活）
        hp = night.get("hidden_poison")
        if hp and hp not in deaths:
            deaths.append(hp)
    else:
        victim = night.get("wolf_target")
        if victim:
            protected = victim == guard or saved
            if victim == guard and saved and state["rules"]["guard_save_conflict_kills"]:
                protected = False
            if not protected:
                deaths.append(victim)

    poisoned = night.get("poison")
    if poisoned and poisoned not in deaths:
        deaths.append(poisoned)
    night["deaths"] = deaths
    state["day"] += 1
    if state["day"] == 1 and state["sheriff_badge"]:
        night["report_deferred"] = True
        _add_event(state, "system", "天亮后先进行警长竞选，昨夜结果将在竞选结束后公布。")
        _start_sheriff_election(state)
        return
    _reveal_night_result(state, "post_night")


def _reveal_night_result(state: Dict[str, Any], resume: str) -> None:
    night = state["night"]
    deaths = list(night.get("deaths", []))
    poisoned = night.get("poison")
    hidden_poisoned = night.get("hidden_poison")
    state["night"]["report_deferred"] = False
    for seat in deaths:
        if seat == poisoned or seat == hidden_poisoned:
            _kill(state, seat, "被毒杀")  # 被毒死，猎人不能开枪
        else:
            _kill(state, seat, "夜间死亡")
    if deaths:
        _add_event(state, "death", "天亮了，昨夜死亡玩家：" + "、".join(f"{seat}号" for seat in deaths) + "。")
    else:
        _add_event(state, "system", "天亮了，昨夜为平安夜。")
    _after_deaths(state, resume)


def _start_sheriff_election(state: Dict[str, Any]) -> None:
    alive = _alive(state)
    # 竞选被第一天自爆打断后，第二天必须从一轮全新的上警选择开始。
    state["sheriff_candidates"] = []
    state["sheriff_election_players"] = []
    if state["human_seat"] in alive:
        alive = [state["human_seat"]] + [seat for seat in alive if seat != state["human_seat"]]
    state["phase"] = "sheriff_campaign"
    state["phase_data"] = {"queue": alive, "index": 0, "action": "campaign",
                           "fixed_allowed": [], "prompt": "请选择上警或不上警。",
                           "candidates": [], "campaign_decisions": {},
                           "simultaneous": True, "published": False}
    _add_event(state, "system", "第一天警长竞选开始，所有存活玩家同时决定是否上警。", phase="sheriff")
    _queue_request(state)


DAY_SPEECH_PHASES = {"sheriff_speech", "sheriff_pk_speech", "day_speech", "day_pk_speech"}


def _can_wolf_explode(state: Dict[str, Any], seat: int) -> bool:
    """Return whether a living ordinary wolf may interrupt the current day."""
    return (
        state.get("phase") in DAY_SPEECH_PHASES
        and seat in _alive(state)
        and _role(state, seat) == "werewolf"
        and state.get("winner") is None
    )


def _wolf_explode(state: Dict[str, Any], seat: int) -> None:
    if not _can_wolf_explode(state, seat):
        raise ValueError("当前阶段不能自爆，只有存活的小狼可在白天发言阶段自爆")
    sheriff_speech = state["phase"] in {"sheriff_speech", "sheriff_pk_speech"}
    if sheriff_speech:
        if state["day"] == 1 and state.get("sheriff_badge"):
            state["sheriff_election_delayed"] = True
        elif state["day"] >= 2 and state.get("sheriff_badge"):
            state["sheriff_badge"] = False
            state["sheriff"] = None
            state["sheriff_candidates"] = []
            state["sheriff_election_players"] = []
            state["sheriff_election_delayed"] = False
            _add_event(state, "sheriff", "第二天警上自爆，警徽被吞，本局不再竞选警长。", seat)
    _player(state, seat)["revealed_role"] = "werewolf"
    _add_event(state, "ability", f"{seat}号狼人自爆，翻牌狼人！白天立即结束，进入黑夜。", seat)
    _kill(state, seat, "狼人自爆", reveal="role")
    # 自爆必须立即打断白天；即使自爆者曾经是警长，也不能再弹出警徽移交。
    if state.get("sheriff") == seat or state.get("badge_transfer_required") == seat:
        state["sheriff"] = None
        state["sheriff_badge"] = False
        state["badge_transfer_required"] = None
    # 第一天警长竞选期间，昨夜结果原本被延迟公布。先结算它，避免开始新夜时
    # 丢失上一夜的死亡记录；结算完成后由 post_explode 进入下一夜。
    if state.get("night", {}).get("report_deferred"):
        _reveal_night_result(state, "post_explode")
    else:
        _after_deaths(state, "post_explode")


def _start_sheriff_speeches(state: Dict[str, Any], candidates: List[int]) -> None:
    candidates = [seat for seat in candidates if seat in _alive(state)]
    state["sheriff_election_players"] = list(candidates)
    state["sheriff_candidates"] = list(candidates)
    if not candidates:
        state["sheriff_badge"] = False
        state["sheriff_candidates"] = []
        _add_event(state, "sheriff", "无人上警，本局没有警长。", phase="sheriff")
        _complete_sheriff_election(state)
        return
    ordered = sorted(candidates)
    digest = hashlib.sha256(f"{state['game_id']}|sheriff-speech".encode()).hexdigest()
    start = int(digest[:8], 16) % len(ordered)
    ordered = ordered[start:] + ordered[:start]
    _add_event(state, "sheriff", "警上随机起点发言顺序：" + "→".join(map(str, ordered)) + "。", phase="sheriff")
    _start_queue(state, "sheriff_speech", "campaign_speech", ordered,
                 prompt="发表警上竞选发言。")
    state["phase_data"]["candidates"] = candidates


def _start_withdraw(state: Dict[str, Any], candidates: List[int]) -> None:
    _start_queue(state, "sheriff_withdraw", "withdraw", candidates,
                 prompt="竞选发言后选择坚持或退水。", simultaneous=True)
    state["phase_data"]["remaining"] = list(candidates)
    state["phase_data"]["withdrawals"] = {}


def _start_sheriff_vote(state: Dict[str, Any], candidates: List[int]) -> None:
    candidates = [seat for seat in candidates if seat in _alive(state)]
    if len(candidates) == 0:
        state["sheriff_badge"] = False
        state["sheriff_candidates"] = []
        _add_event(state, "sheriff", "所有候选人均已退水，警徽被撕毁。", phase="sheriff")
        _complete_sheriff_election(state)
        return
    if len(candidates) == 1:
        _assign_sheriff(state, candidates[0])
        _complete_sheriff_election(state)
        return
    voters = [seat for seat in _alive(state)
              if seat not in state.get("sheriff_election_players", [])]
    _start_queue(state, "sheriff_vote", "vote", voters, allowed=candidates,
                 prompt="警下玩家请选择警长候选人。", simultaneous=True)
    state["phase_data"]["votes"] = {}
    state["phase_data"]["candidates"] = candidates
    state["public_votes"] = {}
    state["vote_summary"] = {}


def _finish_sheriff_vote(state: Dict[str, Any], revote: bool) -> None:
    votes = state["phase_data"].get("votes", {})
    tied = _plurality_ties(votes)
    if len(tied) == 1:
        _assign_sheriff(state, tied[0])
        _complete_sheriff_election(state)
    elif tied and not revote:
        _add_event(state, "sheriff", "警长票平票，进入PK发言：" + "、".join(f"{s}号" for s in tied), phase="sheriff")
        _start_queue(state, "sheriff_pk_speech", "pk_speech", tied,
                     prompt="警长竞选PK发言。")
        state["phase_data"]["tied"] = tied
    else:
        state["sheriff_badge"] = False
        state["sheriff_candidates"] = []
        _add_event(state, "sheriff", "警长PK复投仍平票，警徽被撕毁。", phase="sheriff")
        _complete_sheriff_election(state)


def _assign_sheriff(state: Dict[str, Any], seat: int) -> None:
    state["sheriff"] = seat
    state["sheriff_badge"] = True
    state["sheriff_candidates"] = []
    _add_event(state, "sheriff", f"{seat}号当选警长。", phase="sheriff")


def _complete_sheriff_election(state: Dict[str, Any]) -> None:
    state["sheriff_election_players"] = []
    if state.get("night", {}).get("report_deferred"):
        _reveal_night_result(state, "post_first_night_reveal")
    else:
        _start_day_speech(state)


def _start_day_speech(state: Dict[str, Any]) -> None:
    alive = _alive(state)
    sheriff = state.get("sheriff")
    if sheriff in alive:
        _start_queue(state, "day_order", "sheriff_order", [sheriff],
                     prompt="警长请选择顺序或逆序发言。")
        return
    _begin_speech_queue(state, "forward")


def _begin_speech_queue(state: Dict[str, Any], direction: str) -> None:
    alive = _alive(state)
    sheriff = state.get("sheriff")
    if sheriff in alive:
        if direction == "reverse":
            circle = list(range(sheriff - 1, 0, -1)) + list(range(12, sheriff, -1))
        else:
            circle = list(range(sheriff + 1, 13)) + list(range(1, sheriff))
        order = [seat for seat in circle if seat in alive] + [sheriff]
    else:
        order = list(reversed(alive)) if direction == "reverse" else list(alive)
    direction_text = "从警长上家开始逆序" if direction == "reverse" else "从警长下家开始顺序"
    _add_event(state, "system", f"{direction_text}发言：" + "→".join(map(str, order)) + "。")
    _start_queue(state, "day_speech", "speech", order, prompt="发表本轮公开发言。")


def _start_day_vote(state: Dict[str, Any]) -> None:
    voters = _alive(state)
    _start_queue(state, "day_vote", "vote", voters,
                 prompt="选择今天的放逐目标；可以弃票。", simultaneous=True)
    state["phase_data"]["votes"] = {}
    state["public_votes"] = {}
    state["vote_summary"] = {}


def _finish_day_vote(state: Dict[str, Any], revote: bool) -> None:
    votes = state["phase_data"].get("votes", {})
    weighted: Dict[int, float] = {}
    for voter, target in votes.items():
        if target is None:
            continue
        weight = state["rules"]["sheriff_vote_weight"] if int(voter) == state.get("sheriff") else 1.0
        weighted[target] = weighted.get(target, 0.0) + weight
    tied = _max_keys(weighted)
    if len(tied) == 1:
        out = tied[0]
        state["last_exiled"] = out
        _add_event(state, "vote", f"{out}号被放逐出局。")
        _kill(state, out, "放逐")
        state["phase"] = "last_words"
        state["phase_data"] = {"resume": "post_exile", "exiled": out}
        _request(state, out, "last_words", prompt="你被放逐出局，请发表遗言。")
    elif len(tied) > 1 and not revote:
        _add_event(state, "vote", "放逐票平票，进入PK发言：" + "、".join(f"{s}号" for s in tied))
        _start_queue(state, "day_pk_speech", "pk_speech", tied,
                     prompt="放逐平票PK发言。")
        state["phase_data"]["tied"] = tied
    else:
        _add_event(state, "vote", "复投仍平票，本日无人出局。")
        _after_deaths(state, "post_exile")


def _plurality(votes: Dict[str, Optional[int]], state: Dict[str, Any], salt: str) -> Optional[int]:
    counts: Dict[int, int] = {}
    for target in votes.values():
        if target is not None:
            counts[target] = counts.get(target, 0) + 1
    tied = _max_keys(counts)
    if not tied:
        return None
    digest = hashlib.sha256(f"{state['game_id']}|{state['day']}|{salt}".encode()).hexdigest()
    return sorted(tied)[int(digest[:8], 16) % len(tied)]


def _plurality_ties(votes: Dict[str, Optional[int]]) -> List[int]:
    counts: Dict[int, int] = {}
    for target in votes.values():
        if target is not None:
            counts[target] = counts.get(target, 0) + 1
    return _max_keys(counts)


def _max_keys(counts: Dict[int, float]) -> List[int]:
    if not counts:
        return []
    maximum = max(counts.values())
    return sorted(key for key, value in counts.items() if value == maximum)


def _kill(state: Dict[str, Any], seat: int, cause: str, reveal: Optional[str] = None) -> None:
    if seat not in _alive(state):
        return
    _player(state, seat)["alive"] = False
    if reveal:
        _player(state, seat)["revealed_role"] = _role(state, seat)
    suffix = f"，公开身份为{ROLE_LABELS[_role(state, seat)]}" if reveal else ""
    _add_event(state, "death", f"{seat}号因{cause}出局{suffix}。")
    if state.get("sheriff") == seat:
        state["badge_transfer_required"] = seat
        state["sheriff"] = None
    if _role(state, seat) == "wolf_beauty":
        target = state["abilities"]["beauty_charm"].get(str(seat))
        if target in _alive(state):
            _add_event(state, "ability", f"狼美人{seat}号死亡，{target}号殉情。")
            _kill(state, target, "狼美人殉情")
    # 猎人：被放逐或被刀可开枪，被毒不能开枪（cause 里含"毒"的不能）。
    # 镜隐迷踪中，隐狼学习猎人后也继承这项死亡触发技能。
    learned_hunter = (
        state.get("board") == "mirror"
        and _role(state, seat) == "hidden_wolf"
        and state["abilities"]["hidden_wolf_learned"].get(str(seat)) == "hunter"
    )
    if (_role(state, seat) == "hunter" or learned_hunter) \
            and not state["abilities"]["hunter_shots"].get(str(seat)):
        if "毒" not in cause:
            state.setdefault("_hunter_shots_pending", []).append(seat)


def _winner(state: Dict[str, Any]) -> Optional[str]:
    alive = _alive(state)
    if not any(_role(state, seat) in WOLF_ROLES for seat in alive):
        return "好人阵营"
    if not any(_role(state, seat) in GOD_ROLES for seat in alive):
        return "狼人阵营"
    if not any(_role(state, seat) == "villager" for seat in alive):
        return "狼人阵营"
    return None


def _finish_if_needed(state: Dict[str, Any]) -> bool:
    winner = _winner(state)
    if winner is None:
        return False
    state["winner"] = winner
    _add_event(state, "system", f"游戏结束：{winner}胜利，全部身份已公开。",
               phase="post_game_speech")
    _start_postgame_debrief(state)
    return True


def _start_postgame_debrief(state: Dict[str, Any]) -> None:
    _start_queue(
        state, "post_game_speech", "postgame_speech", range(1, 13),
        prompt=("赛后身份已经全部公开，请先以最终身份表为准，说说自己的感想、关键判断和整局思路；"
                "再自然评价2至4位给你留下特别印象的选手，说明具体原因。"),
        include_dead=True,
    )


def _start_mvp_vote(state: Dict[str, Any]) -> None:
    state["mvp_votes"] = {}
    state["mvp_result"] = {}
    _start_queue(
        state, "mvp_vote", "mvp_vote", range(1, 13),
        prompt=("请公正投给本局表现最出色的一位玩家，可以投自己；"
                "请用一到两句话简要说明可核对的具体理由。"),
        simultaneous=True, include_dead=True,
    )
    state["phase_data"]["votes"] = {}


def migrate_legacy_postgame(state: Dict[str, Any]) -> bool:
    """Move an old finished save into the debrief exactly once."""
    if state.get("phase") != "game_over" or not state.get("winner"):
        return False
    already_started = any(event.get("phase") == "post_game_speech"
                          for event in state.get("history", []))
    if already_started:
        if state.get("mvp_result"):
            return False
        _add_event(state, "system", "为本局补充全员MVP票选。", phase="mvp_vote")
        _start_mvp_vote(state)
        return True
    _add_event(state, "system", "检测到旧版结算存档，现进入赛后全员复盘。",
               phase="post_game_speech")
    _start_postgame_debrief(state)
    return True


def _after_deaths(state: Dict[str, Any], resume: str) -> None:
    # 猎人开枪：先处理待开枪的猎人（在胜负判定之前）
    pending_shots = state.pop("_hunter_shots_pending", [])
    if pending_shots:
        hunter = pending_shots[0]
        alive_targets = [seat for seat in _alive(state) if seat != hunter]
        if alive_targets:
            state["_hunter_shoot_resume"] = resume
            state["phase"] = "hunter_shoot"
            state["phase_data"] = {"resume": resume, "hunter": hunter}
            _request(state, hunter, "hunter_shoot", alive_targets,
                     "你是猎人，死亡时可开枪带走一名玩家；也可选择不开枪。", allow_none=True)
            return
        # 没有可开枪目标 → 直接继续
    if _finish_if_needed(state):
        return
    dead_sheriff = state.get("badge_transfer_required")
    if dead_sheriff:
        interrupted = None
        if resume == "post_duel_fail":
            interrupted = {"phase": state["phase"], "phase_data": deepcopy(state["phase_data"])}
        state["phase"] = "sheriff_transfer"
        state["phase_data"] = {"resume": resume, "interrupted": interrupted}
        _request(state, dead_sheriff, "sheriff_transfer", _targets_for(state, dead_sheriff, "sheriff_transfer"),
                 "警长死亡，请选择移交警徽或撕毁警徽。", allow_none=True)
        return
    _resume(state, resume)


def _resume(state: Dict[str, Any], resume: str) -> None:
    if resume == "post_night":
        if state.get("sheriff_election_delayed") and state.get("sheriff_badge"):
            state["sheriff_election_delayed"] = False
            _start_sheriff_election(state)
        elif state["day"] == 1 and state["sheriff_badge"]:
            _start_sheriff_election(state)
        else:
            _start_day_speech(state)
    elif resume in {"post_exile", "post_duel_success"}:
        _start_night(state)
    elif resume == "post_explode":
        _start_night(state)
    elif resume == "post_duel_fail":
        _queue_request(state)
    elif resume == "post_first_night_reveal":
        _start_day_speech(state)
    else:
        raise RuntimeError(f"未知恢复点: {resume}")


def get_visible_state(state: Dict[str, Any], seat: int) -> Dict[str, Any]:
    """Return the only state a human or AI in ``seat`` is allowed to see."""
    if state.get("schema") != 2 or state.get("phase") == "setup":
        return deepcopy(state)
    own_role = _role(state, seat)
    roles_revealed = state.get("winner") is not None
    if state.get("board") == "mirror":
        # 镜隐迷踪：隐狼与小狼互不知身份
        if own_role == "hidden_wolf":
            known_wolf_team = set()
        elif own_role == "werewolf":
            known_wolf_team = {other for other in range(1, 13)
                               if _role(state, other) == "werewolf" and other != seat}
        else:
            known_wolf_team = set()
    else:
        known_wolf_team = ({other for other in range(1, 13)
                            if _role(state, other) in WOLF_ROLES}
                           if own_role in WOLF_ROLES else set())
    human_viewer = seat == state["human_seat"]
    players = []
    simultaneous_hidden = (
        state.get("phase_data", {}).get("simultaneous")
        and not state.get("phase_data", {}).get("published")
    )
    for other in range(1, 13):
        revealed = _player(state, other).get("revealed_role")
        profile = state.get("ai_profiles", {}).get(str(other))
        is_human_seat_for_viewer = human_viewer and other == state["human_seat"]
        players.append({
            "seat": other, "alive": _player(state, other)["alive"],
            "is_human": is_human_seat_for_viewer,
            "profile_id": ((profile.get("id") if profile else "human")
                           if human_viewer else None),
            "name": profile.get("name") if profile else HUMAN_DISPLAY_NAME,
            "avatar": profile.get("avatar") if profile else HUMAN_AVATAR,
            "sheriff": other == state.get("sheriff"),
            "role": (_role(state, other)
                     if roles_revealed or other == seat or revealed or other in known_wolf_team
                     else None),
            "revealed_role": revealed,
            "campaigning": other in state.get("sheriff_candidates", []),
            "has_voted": (
                False if simultaneous_hidden
                else str(other) in state.get("phase_data", {}).get("votes", {})
            ),
        })
    private: Dict[str, Any] = {}
    if own_role in WOLF_ROLES:
        if own_role == "hidden_wolf":
            # 隐狼不互通：不知道狼队友，也不看小狼的夜聊
            private["wolf_teammates"] = []
        else:
            private["wolf_teammates"] = sorted(known_wolf_team - {seat})
            private["wolf_chat"] = deepcopy(state["wolf_chat"])
    if own_role == "seer":
        private["seer_checks"] = deepcopy(state["abilities"]["seer_checks"].get(str(seat), []))
    if own_role == "witch":
        private["medicine"] = state["abilities"]["witch_medicine"]
        private["poison"] = state["abilities"]["witch_poison"]
        if state["phase"] in {"night_witch_save", "night_witch_poison"}:
            private["tonight_wolf_target"] = state["night"].get("wolf_target")
    if own_role == "guard":
        private["last_guarded"] = state["abilities"]["guard_last"]
    if own_role == "knight":
        private["duel_available"] = not state["abilities"]["knight_used"] and _player(state, seat)["alive"]
    if own_role == "wolf_beauty":
        private["charmed_player"] = state["abilities"]["beauty_charm"].get(str(seat))
    if own_role == "hidden_wolf":
        private["learned_role"] = state["abilities"]["hidden_wolf_learned"].get(str(seat))
        if private["learned_role"] == "seer":
            private["hidden_checks"] = deepcopy(state["abilities"]["hidden_wolf_checks"].get(str(seat), []))
        if private["learned_role"] == "witch":
            private["hidden_poison"] = state["abilities"]["hidden_wolf_poison"]
        if private["learned_role"] == "guard":
            private["hidden_guard_last"] = state["abilities"]["hidden_wolf_guard_last"]
        # 活着的隐狼仍与小狼互不知身份；真人出局后进入观战，可查看已记录的狼聊。
        # 只给真人视角开放，避免把额外的死后信息传给 AI。
        if seat == state["human_seat"] and not _player(state, seat)["alive"]:
            private["wolf_chat"] = deepcopy(state["wolf_chat"])
    if own_role == "mirror_maiden":
        private["mirror_peeks"] = deepcopy(state["abilities"].get("mirror_peeks", {}).get(str(seat), []))
    if own_role == "hunter":
        private["shot_available"] = not state["abilities"]["hunter_shots"].get(str(seat))
    if own_role == "hidden_wolf" and private.get("learned_role") == "hunter":
        private["shot_available"] = not state["abilities"]["hunter_shots"].get(str(seat))
    return {
        "schema": 2, "game_id": state["game_id"], "day": state["day"], "phase": state["phase"],
        "rules": deepcopy(state["rules"]), "players": players, "history": deepcopy(state["history"]),
        "self": {"seat": seat, "role": own_role, "role_label": ROLE_LABELS[own_role],
                 "alive": _player(state, seat)["alive"]},
        "sheriff": state.get("sheriff"), "sheriff_badge": state["sheriff_badge"],
        "last_duel": deepcopy(state.get("last_duel")),
        "public_votes": ({} if state.get("phase_data", {}).get("simultaneous")
                         and not state.get("phase_data", {}).get("published")
                         else deepcopy(state.get("public_votes", {}))),
        "vote_summary": deepcopy(state.get("vote_summary", {})),
        "mvp_result": deepcopy(state.get("mvp_result", {})),
        "private": private,
        "memory": compact_memory(state["ai_memories"].get(str(seat), {})),
        "winner": state["winner"],
    }


def public_state_for_human(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("phase") == "setup":
        return deepcopy(state)
    result = get_visible_state(state, state["human_seat"])
    result["human_seat"] = state["human_seat"]
    result["board"] = state.get("board", "classic")
    raw_pending = state["pending"]
    result["can_human_act"] = bool(raw_pending and raw_pending["actor"] == state["human_seat"])
    result["can_advance_ai"] = bool(raw_pending and raw_pending["actor"] != state["human_seat"])
    pending = deepcopy(raw_pending)
    private_night = state["phase"].startswith("night_")
    simultaneous_wait = bool(state.get("phase_data", {}).get("simultaneous"))
    human_is_wolf = _role(state, state["human_seat"]) in WOLF_ROLES
    wolf_channel = state["phase"] in {"night_wolf_chat", "night_wolf_vote"}
    # 镜隐迷踪的觉醒隐狼与小狼互不知身份，也不能看到狼聊中是谁发言、
    # 当前轮到谁思考。即使真人自己是隐狼，也要按旁观者视角隐藏队伍进度。
    if state.get("board") == "mirror" and _role(state, state["human_seat"]) == "hidden_wolf":
        wolf_channel = False
    if pending and not result["can_human_act"] and (
        (private_night and not (human_is_wolf and wolf_channel)) or simultaneous_wait
    ):
        pending = {
            "actor": None, "action": "simultaneous_wait" if simultaneous_wait else "night_wait",
            "allowed_targets": [],
            "prompt": ("其他玩家正在同时做出决定，完成后将一次性公布。"
                       if simultaneous_wait else "其他夜间角色正在行动，你看不到其身份和选择。"),
            "sequence": raw_pending["sequence"],
            "waiting_external": raw_pending.get("waiting_external", False),
        }
    result["pending"] = pending
    result["can_duel"] = (
        result["self"]["role"] == "knight" and result["self"]["alive"]
        and result["private"].get("duel_available") and state["phase"] == "day_speech"
    )
    result["can_wolf_explode"] = _can_wolf_explode(state, state["human_seat"])
    return result


class HumanGameEngine:
    def __init__(self, provider: ActionProvider):
        self.provider = provider

    def advance_ai(self, state: Dict[str, Any]) -> None:
        pending = state.get("pending")
        if not pending or state["phase"] == "game_over":
            raise ValueError("当前没有可推进的AI行动")
        if pending["actor"] == state["human_seat"]:
            raise ValueError("当前必须等待真人操作")
        if not getattr(self.provider, "batch_actions", True):
            self._advance_one_ai(state)
            return
        if state["phase"].startswith("night_"):
            self._drain_night_ai(state)
            return
        if state.get("phase_data", {}).get("simultaneous"):
            if state["phase"] == "mvp_vote":
                self._advance_one_ai(state)
                return
            self._drain_simultaneous_ai(state)
            return
        if not self._advance_one_ai(state):
            return
        if (state.get("pending") and state["phase"].startswith("night_")
                and state["pending"]["actor"] != state["human_seat"]):
            self._drain_night_ai(state)

    def _advance_one_ai(self, state: Dict[str, Any]) -> bool:
        pending = state["pending"]
        actor = pending["actor"]
        request = deepcopy(pending)
        intent = self.provider.request_action(get_visible_state(state, actor), request)
        if intent is None:
            state["pending"]["waiting_external"] = True
            return False
        self._apply(state, actor, intent)
        return True

    def _drain_simultaneous_ai(self, state: Dict[str, Any]) -> None:
        while (state.get("pending") and state["phase"] != "game_over"
               and state.get("phase_data", {}).get("simultaneous")
               and state["pending"]["actor"] != state["human_seat"]):
            if not self._advance_one_ai(state):
                return

    def _drain_night_ai(self, state: Dict[str, Any]) -> None:
        while (state.get("pending") and state["phase"] != "game_over"
               and state["phase"].startswith("night_")
               and state["pending"]["actor"] != state["human_seat"]):
            if not self._advance_one_ai(state):
                return

    def submit_human(self, state: Dict[str, Any], intent: Dict[str, Any]) -> None:
        action = intent.get("action")
        if action == "knight_duel":
            self._knight_duel(state, state["human_seat"], intent.get("target"))
            return
        if action == "wolf_explode":
            _wolf_explode(state, state["human_seat"])
            return
        pending = state.get("pending")
        if not pending or pending["actor"] != state["human_seat"]:
            raise ValueError("当前没有轮到真人操作")
        self._apply(state, state["human_seat"], intent)
        if not getattr(self.provider, "batch_actions", True):
            return
        if (state.get("pending") and state.get("phase_data", {}).get("simultaneous")
                and state["phase"] != "mvp_vote"
                and state["pending"]["actor"] != state["human_seat"]):
            self._drain_simultaneous_ai(state)
        elif (state.get("pending") and state["phase"].startswith("night_")
              and state["pending"]["actor"] != state["human_seat"]):
            self._drain_night_ai(state)

    def _apply(self, state: Dict[str, Any], actor: int, intent: Dict[str, Any]) -> None:
        pending = state["pending"]
        expected = pending["action"]
        action = intent.get("action")
        if action != expected:
            raise ValueError(f"当前需要的操作是 {expected}")
        allowed = pending.get("allowed_targets", [])

        if action == "wolf_chat":
            text = _clean_text(intent.get("text"), allow_empty=True)
            if text:
                item = {"day": state["day"] + 1, "speaker": actor, "text": text}
                state["wolf_chat"].append(item)
                _remember(state, actor, "note", item)
        elif action == "wolf_kill":
            target = _target(intent, allowed)
            state["night"]["wolf_votes"][str(actor)] = target
        elif action == "charm":
            target = _target(intent, allowed)
            state["abilities"]["beauty_charm"][str(actor)] = target
        elif action == "guard":
            target = _target(intent, allowed)
            state["night"]["guard"] = target
            state["abilities"]["guard_last"] = target
        elif action == "divine":
            target = _target(intent, allowed)
            check = {"day": state["day"] + 1, "seat": target, "is_wolf": _role(state, target) in WOLF_ROLES}
            state["abilities"]["seer_checks"].setdefault(str(actor), []).append(check)
        elif action == "hidden_learn":
            target = _target(intent, allowed)
            learned = _role(state, target)
            state["abilities"]["hidden_wolf_learned"][str(actor)] = learned
            _remember(state, actor, "note", {"day": state["day"] + 1, "learned": learned, "target": target})
        elif action == "hidden_blade":
            # 隐狼带刀：学狼人可双刀（两个不同目标）
            learned = state["abilities"]["hidden_wolf_learned"].get(str(actor))
            if learned in {"werewolf"}:
                t1 = _target(intent, allowed)
                t2 = _optional_target(intent, allowed, key="second_target")
                if t2 is not None and t2 == t1:
                    raise ValueError("双刀必须选择两个不同的目标")
                state["night"]["wolf_targets"] = [t1] + ([t2] if t2 is not None else [])
            else:
                t1 = _target(intent, allowed)
                state["night"]["wolf_targets"] = [t1]
        elif action == "hidden_skill":
            skill = state["phase_data"].get("skill")
            target = _optional_target(intent, allowed)
            if skill == "seer":
                if target is None:
                    raise ValueError("请选择查验目标")
                check = {"day": state["day"] + 1, "seat": target,
                         "is_wolf": _role(state, target) in WOLF_ROLES}
                state["abilities"]["hidden_wolf_checks"].setdefault(str(actor), []).append(check)
            elif skill == "guard":
                if target is None:
                    target = _target(intent, allowed)
                state["night"]["hidden_guard"] = target
                state["abilities"]["hidden_wolf_guard_last"] = target
            elif skill == "poison":
                if target is not None:
                    state["abilities"]["hidden_wolf_poison"] = False
                    state["night"]["hidden_poison"] = target
        elif action == "mirror_peek":
            target = _target(intent, allowed)
            real = _role(state, target)
            if real == "hidden_wolf":
                # 隐狼：显示它学到的角色（学什么显示什么）
                learned = state["abilities"]["hidden_wolf_learned"].get(str(target))
                shown = HIDDEN_WOLF_LEARNABLE.get(learned, learned)
            else:
                shown = real
            check = {"day": state["day"] + 1, "seat": target, "shown_role": shown}
            state["abilities"].setdefault("mirror_peeks", {}).setdefault(str(actor), []).append(check)
        elif action == "witch_save":
            use = bool(intent.get("use"))
            if use and not state["abilities"]["witch_medicine"]:
                raise ValueError("解药已经使用")
            if use and not state["night"].get("wolf_target"):
                raise ValueError("今晚没有可救的狼人刀口")
            if use:
                state["abilities"]["witch_medicine"] = False
                state["night"]["saved"] = True
        elif action == "witch_poison":
            target = _optional_target(intent, allowed)
            if target is not None:
                state["abilities"]["witch_poison"] = False
                state["night"]["poison"] = target
        elif action == "campaign":
            joins = bool(intent.get("join"))
            state["phase_data"]["campaign_decisions"][str(actor)] = joins
            if joins:
                state["phase_data"]["candidates"].append(actor)
        elif action in {"speech", "campaign_speech", "pk_speech", "postgame_speech"}:
            text = _clean_text(intent.get("text"))
            display_phase = "sheriff" if state["phase"].startswith("sheriff") else state["phase"]
            _add_event(state, "speech", text, actor, phase=display_phase)
            _remember(state, actor, "speech", text)
            if action == "postgame_speech" and actor != state["human_seat"]:
                impressions = []
                for item in intent.get("player_impressions") or []:
                    try:
                        target = int(item.get("seat"))
                    except (AttributeError, TypeError, ValueError):
                        continue
                    impression = " ".join(str(item.get("impression") or "").split())[:180]
                    if target in range(1, 13) and target != actor and impression:
                        impressions.append({"seat": target, "impression": impression})
                    if len(impressions) == 4:
                        break
                state.setdefault("postgame_impressions", {})[str(actor)] = impressions
        elif action == "mvp_vote":
            target = _target(intent, allowed)
            reason = " ".join(str(intent.get("text") or "").split())[:160]
            if not reason:
                raise ValueError("MVP票选理由不能为空")
            state["phase_data"].setdefault("votes", {})[str(actor)] = {
                "target": target, "reason": reason,
            }
        elif action == "withdraw":
            withdraws = bool(intent.get("withdraw"))
            state["phase_data"]["withdrawals"][str(actor)] = withdraws
            if withdraws:
                if actor in state["phase_data"]["remaining"]:
                    state["phase_data"]["remaining"].remove(actor)
        elif action == "vote":
            target = _optional_target(intent, allowed)
            state["phase_data"]["votes"][str(actor)] = target
            _remember(state, actor, "vote", target)
            if not state["phase_data"].get("simultaneous"):
                state["public_votes"][str(actor)] = target
                text = f"{actor}号投给{target}号。" if target else f"{actor}号弃票。"
                _add_event(state, "vote", text, actor)
        elif action == "sheriff_order":
            direction = intent.get("direction")
            if direction not in {"forward", "reverse"}:
                raise ValueError("发言方向必须是 forward 或 reverse")
            _begin_speech_queue(state, direction)
            return
        elif action == "sheriff_recommend":
            target = _target(intent, allowed)
            state["phase_data"]["recommend"] = target
            _add_event(state, "sheriff", f"警长{actor}号归票{target}号。", actor)
        elif action == "sheriff_transfer":
            target = _optional_target(intent, allowed)
            if target is None:
                state["sheriff_badge"] = False
                _add_event(state, "sheriff", f"{actor}号撕毁警徽。")
            else:
                state["sheriff"] = target
                _add_event(state, "sheriff", f"{actor}号将警徽移交给{target}号。")
            state["badge_transfer_required"] = None
            resume = state["phase_data"]["resume"]
            interrupted = state["phase_data"].get("interrupted")
            if interrupted:
                state["phase"] = interrupted["phase"]
                state["phase_data"] = interrupted["phase_data"]
            _resume(state, resume)
            return
        elif action == "hunter_shoot":
            target = _optional_target(intent, allowed)
            hunter = state["phase_data"]["hunter"]
            resume = state["phase_data"]["resume"]
            state["abilities"]["hunter_shots"][str(hunter)] = True
            state["phase"] = state["phase_data"].get("prev_phase", "day_speech")
            if target is not None:
                _add_event(state, "ability", f"猎人{hunter}号开枪，带走{target}号。", hunter)
                _kill(state, target, "猎人枪击")
                # 被枪击的也可能是猎人 → 递归开枪
                if state.get("_hunter_shots_pending"):
                    shot = state.pop("_hunter_shots_pending")[0]
                    state.setdefault("_hunter_shots_pending", []).append(shot)
            _after_deaths(state, resume)
            return
        elif action == "last_words":
            text = _clean_text(intent.get("text"))
            _add_event(state, "speech", text, actor, phase="last_words")
            _remember(state, actor, "speech", text)
            _after_deaths(state, state["phase_data"]["resume"])
            return
        else:
            raise ValueError(f"未知操作: {action}")
        if action == "speech" and state["phase"] == "day_speech":
            state["phase_data"]["index"] += 1
            if self._maybe_ai_knight_duel(state):
                return
            _queue_request(state)
            return
        _advance_queue(state)

    def _maybe_ai_knight_duel(self, state: Dict[str, Any]) -> bool:
        # 不再按 provider 名字跳过：桥接模式下由 Worker 预先写好 responses/<id>.json，
        # 这里的同步调用就能拿到决定；没预写时返回 None，自然视作"这一拍不决斗"。
        if state["abilities"]["knight_used"]:
            return False
        knights = [seat for seat in _alive_role(state, "knight") if seat != state["human_seat"]]
        if not knights:
            return False
        knight = knights[0]
        allowed = [seat for seat in _alive(state) if seat != knight]
        request = {"actor": knight, "action": "knight_decide", "allowed_targets": allowed,
                   "prompt": "听完刚才的发言后，决定是否发动骑士决斗。",
                   "sequence": state["action_seq"] + 1}
        intent = self.provider.request_action(get_visible_state(state, knight), request)
        if not intent or intent.get("target") in (None, "", "none"):
            return False
        target = _target(intent, allowed)
        self._knight_duel(state, knight, target)
        return True

    def _knight_duel(self, state: Dict[str, Any], actor: int, raw_target: Any) -> None:
        if state["phase"] != "day_speech" or _role(state, actor) != "knight":
            raise ValueError("当前不能发动骑士决斗")
        if state["abilities"]["knight_used"] or actor not in _alive(state):
            raise ValueError("骑士决斗已经使用或骑士已出局")
        target = _coerce_target(raw_target)
        if target not in _alive(state) or target == actor:
            raise ValueError("决斗目标无效")
        state["abilities"]["knight_used"] = True
        _player(state, actor)["revealed_role"] = "knight"
        _add_event(state, "ability", f"{actor}号骑士翻牌，向{target}号发起决斗！", actor)
        success = _role(state, target) in WOLF_ROLES
        if success:
            _kill(state, target, "骑士决斗", reveal="role")
            state["last_duel"] = {
                "event_id": state["event_seq"], "knight": actor, "target": target,
                "loser": target, "success": True,
            }
            _after_deaths(state, "post_duel_success")
        else:
            _kill(state, actor, "骑士决斗失败", reveal="role")
            state["last_duel"] = {
                "event_id": state["event_seq"], "knight": actor, "target": target,
                "loser": actor, "success": False,
            }
            _after_deaths(state, "post_duel_fail")


def _clean_text(value: Any, allow_empty: bool = False) -> str:
    text = str(value or "").strip()[:2000]
    if not text and not allow_empty:
        raise ValueError("发言不能为空")
    return text


def _coerce_target(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("目标必须是有效座位号")


def _target(intent: Dict[str, Any], allowed: List[int]) -> int:
    target = _coerce_target(intent.get("target"))
    if target not in allowed:
        raise ValueError("目标不在当前合法范围内")
    return target


def _optional_target(intent: Dict[str, Any], allowed: List[int], key: str = "target") -> Optional[int]:
    value = intent.get(key)
    if value in (None, "", "abstain", "none"):
        return None
    return _target({"target": value}, allowed)
