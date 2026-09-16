"""Player-scoped views shared by legacy game integrations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def get_legacy_visible_state(game_state: Dict[str, Any], players: Dict[str, Any],
                             player_id: str) -> Dict[str, Any]:
    """Remove identities and private judge data before a legacy AI call."""
    visible = deepcopy(game_state)
    viewer = players.get(player_id)
    is_wolf = bool(viewer and viewer.is_wolf())
    wolf_ids = {pid for pid, role in players.items() if role.is_wolf()}
    for pid, info in visible.get("players", {}).items():
        if pid != player_id and not (is_wolf and pid in wolf_ids):
            info.pop("role", None)
        info.pop("ai_model", None)

    public_history = []
    for event in visible.get("history", []):
        if event.get("phase") == "night":
            if event.get("event") == "death":
                public_history.append({
                    "round": event.get("round"), "phase": "daybreak",
                    "event": "death", "player": event.get("player"),
                })
            continue
        clean = deepcopy(event)
        for key in ("role", "voter_role", "target_role", "target_is_wolf",
                    "is_correct", "is_wolf", "success"):
            clean.pop(key, None)
        public_history.append(clean)
    visible["history"] = public_history
    visible["discussions"] = [
        {"player": event.get("player"), "content": event.get("content", "")}
        for event in public_history
        if event.get("phase") in {"discussion", "tiebreaker_speech", "sheriff_campaign"}
        and event.get("content")
    ]
    if "current_discussion" in visible:
        visible["current_discussion"] = [
            {key: value for key, value in item.items() if key != "role"}
            for item in visible["current_discussion"]
        ]
    for key in ("alive_count", "votes", "designated_target", "guard_target", "last_guard_target"):
        visible.pop(key, None)
    checks = visible.get("seer_checks", {})
    visible["seer_checks"] = {player_id: checks.get(player_id, {})} if player_id in checks else {}
    return visible
