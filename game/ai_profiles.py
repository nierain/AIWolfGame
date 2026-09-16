"""Persistent identities and compact cross-game memories for computer players."""

from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


HUMAN_DISPLAY_NAME = "牢雨"
HUMAN_AVATAR = "/assets/avatars/player_12.jpg"


PROFILE_TEMPLATES = [
    {"id": "ai_01", "name": "牢翔", "avatar": "/assets/avatars/ai_01.jpg",
     "personality": "幽默观察派", "speech_style": "先活跃气氛，再指出最反常的细节", "risk_style": "中等"},
    {"id": "ai_02", "name": "牢杰", "avatar": "/assets/avatars/ai_02.jpg",
     "personality": "安静逻辑派", "speech_style": "语气温和，按时间线复盘矛盾", "risk_style": "保守"},
    {"id": "ai_03", "name": "牢军", "avatar": "/assets/avatars/ai_03.jpg",
     "personality": "敏锐状态派", "speech_style": "关注语气、站边变化与临场反应", "risk_style": "中等"},
    {"id": "ai_04", "name": "牢宝", "avatar": "/assets/avatars/ai_04.jpg",
     "personality": "自信控场派", "speech_style": "结论清楚，喜欢给出简洁归票", "risk_style": "积极"},
    {"id": "ai_05", "name": "牢恒", "avatar": "/assets/avatars/ai_05.jpg",
     "personality": "谨慎观察派", "speech_style": "先保留意见，证据充分后再站边", "risk_style": "保守"},
    {"id": "ai_06", "name": "牢桐", "avatar": "/assets/avatars/ai_06.jpg",
     "personality": "直觉冲锋派", "speech_style": "表达直接，敢于第一时间点名质疑", "risk_style": "激进"},
    {"id": "ai_07", "name": "牢坤", "avatar": "/assets/avatars/ai_07.jpg",
     "personality": "担当行动派", "speech_style": "重视团队配合，倾向推动明确方案", "risk_style": "积极"},
    {"id": "ai_08", "name": "牢熙", "avatar": "/assets/avatars/ai_08.jpg",
     "personality": "松弛分析派", "speech_style": "语气轻松，但会持续核对票型和发言", "risk_style": "中等"},
    {"id": "ai_09", "name": "牢灵", "avatar": "/assets/avatars/ai_09.jpg",
     "personality": "冷静推演派", "speech_style": "擅长列出多种可能并逐一排除", "risk_style": "保守"},
    {"id": "ai_10", "name": "牢林", "avatar": "/assets/avatars/ai_10.jpg",
     "personality": "灵活操作派", "speech_style": "会试探、变换策略，并解释改站边原因", "risk_style": "积极"},
    {"id": "ai_11", "name": "牢威", "avatar": "/assets/avatars/ai_11.jpg",
     "personality": "外向互动派", "speech_style": "积极回应其他玩家，善于追问关键问题", "risk_style": "中等"},
    {"id": "ai_12", "name": "牢咕", "avatar": "/assets/avatars/ai_12.jpg",
     "personality": "神秘冷静派", "speech_style": "先听完整轮发言，再从细节中给出独立判断", "risk_style": "中等"},
]


def default_memory_store() -> Dict[str, Any]:
    return {
        "schema": 1,
        "archived_game_ids": [],
        "profiles": {
            profile["id"]: {
                "games_played": 0,
                "wins": 0,
                "losses": 0,
                "summary": "还没有参加过已完成的对局。",
                "recent_games": [],
            }
            for profile in PROFILE_TEMPLATES
        },
    }


def load_memory_store(path: Path) -> Dict[str, Any]:
    store = default_memory_store()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return store
    if not isinstance(loaded, dict):
        return store
    store["archived_game_ids"] = list(dict.fromkeys(loaded.get("archived_game_ids", [])))
    loaded_profiles = loaded.get("profiles", {})
    for profile_id, defaults in store["profiles"].items():
        saved = loaded_profiles.get(profile_id, {})
        if not isinstance(saved, dict):
            continue
        defaults.update({key: saved[key] for key in defaults if key in saved})
        defaults["recent_games"] = list(defaults.get("recent_games", []))[-5:]
    return store


def _write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def assign_profiles(human_seat: int, store: Optional[Mapping[str, Any]] = None,
                    profile_ids: Optional[list] = None) -> Dict[str, Dict[str, Any]]:
    """Assign eleven selected identities to the eleven non-human seats."""
    saved_profiles = (store or {}).get("profiles", {})
    seats = [seat for seat in range(1, 13) if seat != human_seat]
    templates_by_id = {profile["id"]: profile for profile in PROFILE_TEMPLATES}
    selected_ids = profile_ids or [profile["id"] for profile in PROFILE_TEMPLATES[:11]]
    if len(selected_ids) != 11 or len(set(selected_ids)) != 11:
        raise ValueError("每局必须选择11位不同的AI玩家")
    if any(profile_id not in templates_by_id for profile_id in selected_ids):
        raise ValueError("AI玩家档案不存在")
    assignments: Dict[str, Dict[str, Any]] = {}
    for seat, profile_id in zip(seats, selected_ids):
        template = templates_by_id[profile_id]
        saved = saved_profiles.get(template["id"], {})
        profile = deepcopy(template)
        profile["long_term_summary"] = saved.get("summary", "还没有参加过已完成的对局。")
        profile["recent_game_memories"] = deepcopy(saved.get("recent_games", []))[-3:]
        assignments[str(seat)] = profile
    return assignments


def ensure_profiles(state: Dict[str, Any], store: Optional[Mapping[str, Any]] = None) -> bool:
    """Migrate an existing save to persistent profiles and compact its live memory."""
    if state.get("schema") != 2 or state.get("phase") == "setup":
        return False
    changed = False
    seats = [seat for seat in range(1, 13) if seat != int(state["human_seat"])]
    valid_ids = {profile["id"] for profile in PROFILE_TEMPLATES}
    existing_ids = [state.get("ai_profiles", {}).get(str(seat), {}).get("id")
                    for seat in seats]
    if (len(set(existing_ids)) != 11 or
            any(profile_id not in valid_ids for profile_id in existing_ids)):
        rng = random.Random(str(state.get("game_id", "legacy-game")))
        existing_ids = [profile["id"] for profile in rng.sample(PROFILE_TEMPLATES, 11)]
    assignments = assign_profiles(int(state["human_seat"]), store, existing_ids)
    if state.get("ai_profiles") != assignments:
        state["ai_profiles"] = assignments
        changed = True
    memories = state.setdefault("ai_memories", {})
    for seat, profile in assignments.items():
        memory = memories.setdefault(seat, {})
        compact_fields = {
            "profile_id": profile["id"],
            "name": profile["name"],
            "avatar": profile["avatar"],
            "personality": profile["personality"],
            "speech_style": profile["speech_style"],
            "risk_style": profile["risk_style"],
            "long_term_summary": profile["long_term_summary"],
            "recent_game_memories": deepcopy(profile["recent_game_memories"]),
        }
        for key, value in compact_fields.items():
            if memory.get(key) != value:
                memory[key] = value
                changed = True
        if "observations" in memory:
            memory.pop("observations", None)
            changed = True
        memory.setdefault("own_speeches", [])
        memory.setdefault("votes", [])
        memory.setdefault("beliefs", {"suspects": [], "trusted": []})
        memory.setdefault("strategy", "根据公开发言和票型逐轮修正判断")
        memory.setdefault("notes", [])
    return changed


def compact_memory(memory: Mapping[str, Any]) -> Dict[str, Any]:
    """Return only the useful bounded portion included in an AI request."""
    keys = (
        "profile_id", "name", "avatar", "personality", "speech_style", "risk_style",
        "long_term_summary", "recent_game_memories", "beliefs", "strategy",
    )
    result = {key: deepcopy(memory[key]) for key in keys if key in memory}
    result["own_speeches"] = deepcopy(memory.get("own_speeches", []))[-4:]
    result["votes"] = deepcopy(memory.get("votes", []))[-6:]
    result["notes"] = deepcopy(memory.get("notes", []))[-6:]
    return result


def _won(role: str, winner: str) -> bool:
    is_wolf = role in {"werewolf", "wolf_beauty"}
    return (winner == "狼人阵营") == is_wolf


def archive_completed_game(state: Dict[str, Any], memory_path: Path, archive_root: Path) -> bool:
    """Archive one completed game and update each identity's compact memory once."""
    if state.get("phase") != "game_over" or not state.get("winner"):
        return False
    ensure_profiles(state, load_memory_store(memory_path))
    game_id = str(state.get("game_id") or "")
    if not game_id:
        return False
    store = load_memory_store(memory_path)
    if game_id in store["archived_game_ids"]:
        return False

    archive = {
        "schema": 1,
        "game_id": game_id,
        "day": state.get("day", 0),
        "winner": state["winner"],
        "players": [],
        "history": deepcopy(state.get("history", [])),
    }
    for seat in range(1, 13):
        profile = state.get("ai_profiles", {}).get(str(seat))
        archive["players"].append({
            "seat": seat,
            "profile_id": profile.get("id") if profile else None,
            "name": profile.get("name") if profile else HUMAN_DISPLAY_NAME,
            "avatar": profile.get("avatar") if profile else HUMAN_AVATAR,
            "is_human": seat == state.get("human_seat"),
            "role": state.get("roles", {}).get(str(seat)),
            "alive": state.get("players", {}).get(str(seat), {}).get("alive", False),
        })
    _write_json_atomic(archive_root / f"{game_id}.json", archive)

    for seat, profile in state.get("ai_profiles", {}).items():
        profile_id = profile["id"]
        persistent = store["profiles"][profile_id]
        role = state.get("roles", {}).get(str(seat), "unknown")
        won = _won(role, state["winner"])
        reflection = next(
            (event.get("text", "") for event in reversed(state.get("history", []))
             if event.get("phase") == "post_game_speech"
             and event.get("kind") == "speech"
             and event.get("speaker") == int(seat)),
            "本局没有留下赛后复盘。",
        )
        entry = {
            "game_id": game_id,
            "seat": int(seat),
            "role": role,
            "won": won,
            "days": state.get("day", 0),
            "reflection": str(reflection)[:900],
        }
        persistent["games_played"] = int(persistent.get("games_played", 0)) + 1
        persistent["wins"] = int(persistent.get("wins", 0)) + int(won)
        persistent["losses"] = int(persistent.get("losses", 0)) + int(not won)
        recent = list(persistent.get("recent_games", []))
        recent.append(entry)
        persistent["recent_games"] = recent[-5:]
        persistent["summary"] = (
            f"已参加{persistent['games_played']}局，{persistent['wins']}胜"
            f"{persistent['losses']}负。最近一局以{role}身份"
            f"{'获胜' if won else '落败'}；自己的复盘：{entry['reflection']}"
        )[:1400]

    store["archived_game_ids"].append(game_id)
    _write_json_atomic(memory_path, store)
    return True
