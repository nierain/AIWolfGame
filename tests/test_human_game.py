import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from game.ai_providers import BuiltinAIProvider, CodexCLIProvider, CodexFileProvider
from game.ai_profiles import (
    PROFILE_TEMPLATES,
    archive_completed_game,
    default_memory_store,
    ensure_profiles,
    load_memory_store,
)
from game.human_game import (
    GOD_ROLES,
    ROLE_DECK,
    WOLF_ROLES,
    HumanGameEngine,
    _after_deaths,
    _begin_speech_queue,
    _complete_sheriff_election,
    _finish_day_vote,
    _finish_sheriff_vote,
    _kill,
    _queue_complete,
    _resolve_night,
    _start_day_vote,
    _start_sheriff_election,
    _start_sheriff_speeches,
    _start_sheriff_vote,
    _start_withdraw,
    _winner,
    create_game,
    get_visible_state,
    public_state_for_human,
)
from game.roles import Villager, Werewolf
from game.visibility import get_legacy_visible_state
import panel_game


class QuietElectionProvider(BuiltinAIProvider):
    def request_action(self, visible_state, request):
        if request["action"] == "campaign":
            return {"action": "campaign", "join": False}
        if request["action"] == "knight_decide":
            return {"action": "knight_decide", "target": None}
        return super().request_action(visible_state, request)


class KnightStrikeProvider(QuietElectionProvider):
    def __init__(self, target):
        self.target = target

    def request_action(self, visible_state, request):
        if request["action"] == "knight_decide":
            return {"action": "knight_decide", "target": self.target}
        return super().request_action(visible_state, request)


def human_intent(state):
    pending = state["pending"]
    action = pending["action"]
    targets = pending.get("allowed_targets", [])
    if action in {"speech", "campaign_speech", "pk_speech", "last_words", "postgame_speech"}:
        return {"action": action, "text": "我先听完整轮发言，再结合公开票型判断，不会盲目跟票。"}
    if action == "wolf_chat":
        return {"action": action, "text": "今晚先找带队位置，白天注意不要站得太整齐。"}
    if action == "campaign":
        return {"action": action, "join": False}
    if action == "withdraw":
        return {"action": action, "withdraw": False}
    if action == "witch_save":
        return {"action": action, "use": False}
    if action == "witch_poison":
        return {"action": action, "target": None}
    if action == "sheriff_order":
        return {"action": action, "direction": "forward"}
    if action == "sheriff_transfer":
        return {"action": action, "target": targets[0] if targets else None}
    if action == "mvp_vote":
        return {"action": action, "target": targets[0], "text": "关键轮次贡献最直接。"}
    return {"action": action, "target": targets[0] if targets else None}


def drive(state, *, stop=None, limit=1000):
    engine = HumanGameEngine(BuiltinAIProvider())
    for _ in range(limit):
        if stop and stop(state):
            return state
        if state["phase"] == "game_over":
            return state
        if state["pending"]["actor"] == state["human_seat"]:
            engine.submit_human(state, human_intent(state))
        else:
            engine.advance_ai(state)
    raise AssertionError(f"对局未在{limit}步内完成，当前阶段={state['phase']}")


class HumanGameTests(unittest.TestCase):
    def test_existing_schema_two_save_gets_new_candidate_field(self):
        state = create_game(1, "villager", seed=2)
        state.pop("sheriff_candidates")
        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "state.json"
            save_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            with patch.object(panel_game, "STATE_FILE", save_path):
                restored = panel_game.load_state()
            self.assertEqual(restored["sheriff_candidates"], [])

    def test_legacy_game_over_save_migrates_to_postgame_once(self):
        state = create_game(4, "villager", seed=32)
        state["winner"] = "狼人阵营"
        state["phase"] = "game_over"
        state["pending"] = None
        state["phase_data"] = {}
        self.assertFalse(any(event.get("phase") == "post_game_speech"
                             for event in state["history"]))

        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "legacy-game-over.json"
            save_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            with patch.object(panel_game, "STATE_FILE", save_path):
                restored = panel_game.load_state()
                restored_again = panel_game.load_state()

        self.assertEqual(restored["phase"], "post_game_speech")
        self.assertEqual(restored["pending"]["action"], "postgame_speech")
        self.assertEqual(restored["phase_data"]["queue"], list(range(1, 13)))
        self.assertEqual(len([event for event in restored["history"]
                              if event.get("phase") == "post_game_speech"]), 1)
        self.assertEqual(len([event for event in restored_again["history"]
                              if event.get("phase") == "post_game_speech"]), 1)

    def test_completed_debrief_without_mvp_returns_to_mvp_vote(self):
        state = create_game(4, "villager", seed=32)
        state["winner"] = "好人阵营"
        state["phase"] = "game_over"
        state["pending"] = None
        state["phase_data"] = {}
        state["history"].append({
            "id": 999, "day": 3, "phase": "post_game_speech", "kind": "speech",
            "speaker": 1, "text": "本局复盘已经完成。",
        })

        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "completed-without-mvp.json"
            save_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            with patch.object(panel_game, "STATE_FILE", save_path):
                restored = panel_game.load_state()

        self.assertEqual(restored["phase"], "mvp_vote")
        self.assertEqual(restored["pending"]["action"], "mvp_vote")
        self.assertEqual(restored["pending"]["actor"], state["human_seat"])
        self.assertIn(state["human_seat"], restored["pending"]["allowed_targets"])

    def test_standard_board_and_debug_roles(self):
        self.assertEqual(len(ROLE_DECK), 12)
        for wanted in ["villager", "werewolf", "wolf_beauty", "seer", "witch", "guard", "knight"]:
            state = create_game(8, wanted, seed=13)
            self.assertEqual(state["roles"]["8"], wanted)
            roles = list(state["roles"].values())
            self.assertEqual(sum(role in WOLF_ROLES for role in roles), 4)
            self.assertEqual(sum(role in GOD_ROLES for role in roles), 4)
            self.assertEqual(roles.count("villager"), 4)

    def test_random_human_seat_is_not_fixed(self):
        seats = {create_game(0, "random", seed=seed)["human_seat"] for seed in range(20)}
        self.assertGreater(len(seats), 1)
        self.assertTrue(all(1 <= seat <= 12 for seat in seats))

    def test_ai_memories_and_personalities_are_independent_and_persisted(self):
        state = create_game(1, "villager", seed=6)
        memories = state["ai_memories"]
        self.assertGreater(len({item["personality"] for item in memories.values()}), 1)
        first, second = list(memories)[:2]
        memories[first]["beliefs"]["suspects"].append(9)
        self.assertNotIn(9, memories[second]["beliefs"]["suspects"])
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        self.assertEqual(restored["ai_memories"][first]["beliefs"]["suspects"], [9])

    def test_ai_profiles_keep_avatar_personality_and_identity_together(self):
        first_game = create_game(1, "villager", seed=6)
        second_game = create_game(7, "villager", seed=7)
        for state in (first_game, second_game):
            profiles = list(state["ai_profiles"].values())
            self.assertEqual(len(profiles), 11)
            self.assertEqual(len({profile["id"] for profile in profiles}), 11)
            for profile in profiles:
                number = profile["id"].split("_")[1]
                self.assertEqual(profile["avatar"], f"/assets/avatars/ai_{number}.jpg")
                seat = next(seat for seat, item in state["ai_profiles"].items()
                            if item["id"] == profile["id"])
                memory = state["ai_memories"][seat]
                self.assertEqual(memory["personality"], profile["personality"])
                self.assertEqual(memory["avatar"], profile["avatar"])

    def test_each_game_randomly_selects_eleven_of_twelve_named_profiles(self):
        expected_names = [
            "牢翔", "牢杰", "牢军", "牢宝", "牢恒", "牢桐",
            "牢坤", "牢熙", "牢灵", "牢林", "牢威", "牢咕",
        ]
        self.assertEqual([profile["name"] for profile in PROFILE_TEMPLATES], expected_names)
        all_ids = {profile["id"] for profile in PROFILE_TEMPLATES}
        omitted = set()
        for seed in range(20):
            state = create_game(1, "villager", seed=seed)
            selected = {profile["id"] for profile in state["ai_profiles"].values()}
            self.assertEqual(len(selected), 11)
            omitted.update(all_ids - selected)
        self.assertGreater(len(omitted), 1)

    def test_existing_game_keeps_its_selected_profiles_during_migration(self):
        state = create_game(4, "villager", seed=9)
        before = {seat: profile["id"] for seat, profile in state["ai_profiles"].items()}
        ensure_profiles(state, default_memory_store())
        after = {seat: profile["id"] for seat, profile in state["ai_profiles"].items()}
        self.assertEqual(after, before)

    def test_visible_memory_is_compact_and_profiles_are_public(self):
        state = create_game(1, "villager", seed=6)
        ai_seat = next(iter(state["ai_memories"]))
        state["ai_memories"][ai_seat]["observations"] = [{"text": "重复记录"}] * 200
        view = get_visible_state(state, int(ai_seat))
        self.assertNotIn("observations", view["memory"])
        self.assertLessEqual(len(view["memory"]["own_speeches"]), 4)
        player = next(item for item in view["players"] if item["seat"] == int(ai_seat))
        self.assertIsNone(player["profile_id"])
        self.assertEqual(player["name"], view["memory"]["name"])
        self.assertTrue(player["avatar"].startswith("/assets/avatars/"))

    def test_completed_game_is_archived_once_and_updates_compact_memory(self):
        state = create_game(1, "villager", seed=6)
        state["phase"] = "game_over"
        state["winner"] = "狼人阵营"
        state["history"].append({
            "id": 999, "day": 2, "phase": "post_game_speech", "kind": "speech",
            "speaker": 2, "text": "我这一局站边太快，下局会先核对票型。",
        })
        state["postgame_impressions"]["2"] = [
            {"seat": 1, "impression": "发言谨慎，关键轮次能及时修正站边。"}
        ]
        state["mvp_votes"]["2"] = {"target": 1, "reason": "关键轮次贡献稳定。"}
        state["mvp_result"] = {"counts": {"1": 1}, "winners": [1], "votes": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / ".aiwolf_long_term_memory.json"
            archive_dir = root / "game_archives"
            self.assertTrue(archive_completed_game(state, memory_path, archive_dir))
            self.assertFalse(archive_completed_game(state, memory_path, archive_dir))
            state["mvp_votes"]["2"] = {"target": 1, "reason": "补投理由。"}
            state["mvp_result"] = {"counts": {"1": 1}, "winners": [1], "votes": []}
            self.assertTrue(archive_completed_game(state, memory_path, archive_dir))
            store = load_memory_store(memory_path)
            profile_id = state["ai_profiles"]["2"]["id"]
            persistent = store["profiles"][profile_id]
            self.assertEqual(persistent["games_played"], 1)
            self.assertIn("核对票型", persistent["summary"])
            self.assertEqual(persistent["recent_games"][-1]["player_impressions"][0]["seat"], 1)
            self.assertEqual(persistent["recent_games"][-1]["mvp_vote"]["target"], 1)
            self.assertEqual(persistent["recent_games"][-1]["mvp_vote"]["reason"], "补投理由。")
            archive = json.loads((archive_dir / f"{state['game_id']}.json").read_text("utf-8"))
            self.assertEqual(archive["winner"], "狼人阵营")
            self.assertEqual(archive["mvp_result"]["winners"], [1])
            self.assertNotIn("wolf_chat", archive)

    def test_every_night_role_waits_for_human(self):
        expected = {
            "werewolf": "wolf_chat", "wolf_beauty": "wolf_chat", "seer": "divine",
            "witch": "witch_save", "guard": "guard",
        }
        for role, action in expected.items():
            with self.subTest(role=role):
                state = create_game(5, role, seed=24)
                drive(state, stop=lambda s: s["pending"]["actor"] == 5 and s["pending"]["action"] == action)
                self.assertEqual(state["pending"]["action"], action)

    def test_private_information_is_filtered(self):
        state = create_game(1, "seer", seed=7)
        roles = state["roles"]
        view = get_visible_state(state, 1)
        self.assertNotIn("roles", view)
        for player in view["players"]:
            if player["seat"] != 1:
                self.assertIsNone(player["role"])
        wolf = next(int(seat) for seat, role in roles.items() if role in WOLF_ROLES)
        wolf_view = get_visible_state(state, wolf)
        expected_team = {int(seat) for seat, role in roles.items() if role in WOLF_ROLES} - {wolf}
        self.assertEqual(set(wolf_view["private"]["wolf_teammates"]), expected_team)
        for teammate in expected_team:
            teammate_view = next(player for player in wolf_view["players"]
                                 if player["seat"] == teammate)
            self.assertIn(teammate_view["role"], WOLF_ROLES)
        dead_teammate = next(iter(expected_team))
        _kill(state, dead_teammate, "测试出局")
        after_death = get_visible_state(state, wolf)
        self.assertIn(dead_teammate, after_death["private"]["wolf_teammates"])
        self.assertIn(next(player for player in after_death["players"]
                           if player["seat"] == dead_teammate)["role"], WOLF_ROLES)
        villager = next(int(seat) for seat, role in roles.items() if role == "villager")
        self.assertNotIn("wolf_teammates", get_visible_state(state, villager)["private"])

    def test_human_cannot_identify_other_night_roles_from_pending_action(self):
        state = create_game(1, "villager", seed=7)
        public = public_state_for_human(state)
        self.assertIsNone(public["pending"]["actor"])
        self.assertEqual(public["pending"]["action"], "night_wait")
        self.assertNotIn("狼人", public["pending"]["prompt"])

    def test_seer_result_persists_through_json(self):
        state = create_game(4, "seer", seed=2)
        drive(state, stop=lambda s: s["pending"]["actor"] == 4 and s["pending"]["action"] == "divine")
        target = state["pending"]["allowed_targets"][0]
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "divine", "target": target})
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        checks = get_visible_state(restored, 4)["private"]["seer_checks"]
        self.assertEqual(checks[-1]["seat"], target)

    def test_human_controls_each_night_skill(self):
        engine = HumanGameEngine(BuiltinAIProvider())

        state = create_game(2, "werewolf", seed=31)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "wolf_chat")
        self.assertEqual(state["phase_data"]["queue"], [2])
        engine.submit_human(state, {"action": "wolf_chat", "text": "我建议刀警上强势位置。"})
        self.assertEqual(state["pending"]["actor"], 2)
        self.assertEqual(state["pending"]["action"], "wolf_kill")
        self.assertEqual(len(state["wolf_chat"]), 1)
        self.assertEqual(state["wolf_chat"][0]["speaker"], 2)
        self.assertEqual(state["wolf_chat"][0]["text"], "我建议刀警上强势位置。")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "wolf_kill", "target": target})
        self.assertEqual(state["night"]["wolf_votes"], {"2": target})
        self.assertEqual(state["night"]["wolf_target"], target)

        state = create_game(2, "wolf_beauty", seed=31)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "charm")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "charm", "target": target})
        self.assertEqual(state["abilities"]["beauty_charm"]["2"], target)

        state = create_game(2, "guard", seed=31)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "guard")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "guard", "target": target})
        self.assertEqual(state["abilities"]["guard_last"], target)

        state = create_game(2, "witch", seed=31)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "witch_save")
        engine.submit_human(state, {"action": "witch_save", "use": False})
        self.assertEqual(state["pending"]["action"], "witch_poison")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "witch_poison", "target": target})
        self.assertFalse(state["abilities"]["witch_poison"])

    def test_wolf_self_kill_rule(self):
        engine = HumanGameEngine(BuiltinAIProvider())

        state = create_game(2, "werewolf", seed=31)
        drive(state, stop=lambda s: s["pending"]["action"] == "wolf_kill")
        self.assertTrue(state["rules"]["wolf_can_self_kill"])
        self.assertIn(2, state["pending"]["allowed_targets"])
        engine.submit_human(state, {"action": "wolf_kill", "target": 2})
        self.assertEqual(state["night"]["wolf_votes"], {"2": 2})
        self.assertEqual(state["night"]["wolf_target"], 2)

        classic = create_game(2, "werewolf", seed=31, wolf_self_kill=False)
        drive(classic, stop=lambda s: s["pending"]["action"] == "wolf_kill")
        self.assertFalse(classic["rules"]["wolf_can_self_kill"])
        for seat in range(1, 13):
            if classic["roles"][str(seat)] in WOLF_ROLES:
                self.assertNotIn(seat, classic["pending"]["allowed_targets"])

    def test_wolf_beauty_martyrdom_and_win_conditions(self):
        state = create_game(6, "wolf_beauty", seed=9)
        target = next(seat for seat in range(1, 13) if state["roles"][str(seat)] == "villager")
        state["abilities"]["beauty_charm"]["6"] = target
        _kill(state, 6, "测试毒杀")
        self.assertFalse(state["players"]["6"]["alive"])
        self.assertFalse(state["players"][str(target)]["alive"])
        for seat in range(1, 13):
            state["players"][str(seat)]["alive"] = state["roles"][str(seat)] not in WOLF_ROLES
        self.assertEqual(_winner(state), "好人阵营")
        state = create_game(6, "wolf_beauty", seed=9)
        for seat in range(1, 13):
            if state["roles"][str(seat)] in GOD_ROLES:
                state["players"][str(seat)]["alive"] = False
        self.assertEqual(_winner(state), "狼人阵营")
        state = create_game(6, "wolf_beauty", seed=9)
        for seat in range(1, 13):
            if state["roles"][str(seat)] == "villager":
                state["players"][str(seat)]["alive"] = False
        self.assertEqual(_winner(state), "狼人阵营")

    def test_knight_human_can_duel(self):
        state = create_game(3, "knight", seed=11)
        state["day"] = 1
        _begin_speech_queue(state, "forward")
        self.assertEqual(state["pending"]["actor"], 1)
        self.assertTrue(public_state_for_human(state)["can_duel"])
        wolf = next(int(seat) for seat, role in state["roles"].items() if role in WOLF_ROLES)
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "knight_duel", "target": wolf})
        self.assertFalse(state["players"][str(wolf)]["alive"])
        self.assertTrue(state["abilities"]["knight_used"])
        self.assertEqual(state["last_duel"]["loser"], wolf)
        self.assertTrue(state["last_duel"]["success"])

    def test_ai_knight_may_duel_after_any_public_speech(self):
        state = create_game(1, "villager", seed=11)
        knight = next(int(seat) for seat, role in state["roles"].items() if role == "knight")
        wolf = next(int(seat) for seat, role in state["roles"].items() if role in WOLF_ROLES)
        self.assertNotEqual(knight, state["human_seat"])
        state["day"] = 1
        _begin_speech_queue(state, "forward")
        HumanGameEngine(KnightStrikeProvider(wolf)).submit_human(
            state, {"action": "speech", "text": "我先发言，骑士可以在听完后自行决定是否发动。"}
        )
        self.assertFalse(state["players"][str(wolf)]["alive"])
        self.assertTrue(state["abilities"]["knight_used"])

    def test_sheriff_speaks_last_in_both_directions(self):
        forward = create_game(1, "villager", seed=5)
        forward["sheriff"] = 5
        _begin_speech_queue(forward, "forward")
        self.assertEqual(
            forward["phase_data"]["queue"],
            [6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5],
        )

        reverse = create_game(1, "villager", seed=5)
        reverse["sheriff"] = 5
        _begin_speech_queue(reverse, "reverse")
        self.assertEqual(
            reverse["phase_data"]["queue"],
            [4, 3, 2, 1, 12, 11, 10, 9, 8, 7, 6, 5],
        )

    def test_campaign_choices_publish_together_and_mark_candidates(self):
        state = create_game(1, "villager", seed=6)
        engine = HumanGameEngine(QuietElectionProvider())
        _start_sheriff_election(state)
        engine.submit_human(state, {"action": "campaign", "join": True})

        announcements = [
            event for event in state["history"]
            if "上警结果同时公布" in event["text"]
        ]
        self.assertEqual(len(announcements), 1)
        self.assertEqual(state["phase"], "sheriff_speech")
        self.assertEqual(state["sheriff_candidates"], [1])
        candidate = next(p for p in public_state_for_human(state)["players"] if p["seat"] == 1)
        self.assertTrue(candidate["campaigning"])

    def test_votes_are_hidden_until_everyone_has_decided(self):
        hidden = create_game(1, "villager", seed=8)
        _start_day_vote(hidden)
        hidden["phase_data"]["votes"]["2"] = 3
        hidden_view = public_state_for_human(hidden)
        self.assertEqual(hidden_view["public_votes"], {})
        self.assertFalse(next(p for p in hidden_view["players"] if p["seat"] == 2)["has_voted"])

        state = create_game(1, "villager", seed=8)
        _start_day_vote(state)
        HumanGameEngine(QuietElectionProvider()).submit_human(
            state, {"action": "vote", "target": 2}
        )
        announcements = [
            event for event in state["history"]
            if "投票同时公布" in event["text"]
        ]
        self.assertEqual(len(announcements), 1)
        self.assertEqual(announcements[0]["text"].count("→"), 12)

    def test_dead_human_sheriff_chooses_badge_fate(self):
        state = create_game(3, "villager", seed=15)
        state["sheriff"] = 3
        _kill(state, 3, "测试死亡")
        _after_deaths(state, "post_exile")
        self.assertEqual(state["pending"]["action"], "sheriff_transfer")
        self.assertEqual(state["pending"]["actor"], 3)
        HumanGameEngine(BuiltinAIProvider()).submit_human(
            state, {"action": "sheriff_transfer", "target": None}
        )
        self.assertFalse(state["sheriff_badge"])
        self.assertEqual(state["phase"], "day_speech")

    def test_first_night_result_is_revealed_after_sheriff_election(self):
        state = create_game(1, "villager", seed=23)
        victim = next(seat for seat in range(2, 13) if state["roles"][str(seat)] == "villager")
        state["night"] = {
            "wolf_votes": {}, "wolf_target": victim, "guard": None,
            "saved": False, "poison": None, "deaths": [],
        }
        _resolve_night(state)
        self.assertEqual(state["phase"], "sheriff_campaign")
        self.assertTrue(state["players"][str(victim)]["alive"])
        self.assertFalse(any("昨夜死亡玩家" in event["text"] for event in state["history"]))

        HumanGameEngine(QuietElectionProvider()).submit_human(
            state, {"action": "campaign", "join": False}
        )
        self.assertFalse(state["players"][str(victim)]["alive"])
        election_index = next(i for i, event in enumerate(state["history"])
                              if "无人上警" in event["text"])
        report_index = next(i for i, event in enumerate(state["history"])
                            if "昨夜死亡玩家" in event["text"])
        self.assertLess(election_index, report_index)

    def test_only_original_sheriff_non_candidates_can_vote(self):
        state = create_game(12, "villager", seed=12)
        state["sheriff_election_players"] = [1, 2, 3]
        _start_sheriff_vote(state, [1, 3])
        self.assertEqual(set(state["phase_data"]["queue"]), set(range(4, 13)))
        self.assertTrue({1, 2, 3}.isdisjoint(state["phase_data"]["queue"]))

        state["phase"] = "sheriff_pk_speech"
        state["phase_data"] = {"tied": [1, 3]}
        _queue_complete(state)
        self.assertEqual(set(state["phase_data"]["queue"]), set(range(4, 13)))
        self.assertTrue({1, 2, 3}.isdisjoint(state["phase_data"]["queue"]))

    def test_sheriff_speech_order_only_randomizes_start(self):
        state = create_game(1, "villager", seed=9)
        candidates = [2, 5, 9, 11]
        _start_sheriff_speeches(state, candidates)
        order = state["phase_data"]["queue"]
        rotations = [sorted(candidates)[i:] + sorted(candidates)[:i]
                     for i in range(len(candidates))]
        self.assertIn(order, rotations)
        self.assertEqual(set(order), set(candidates))

    def test_one_night_click_runs_until_human_action_or_dawn(self):
        villager = create_game(1, "villager", seed=4)
        HumanGameEngine(BuiltinAIProvider()).advance_ai(villager)
        self.assertEqual(villager["phase"], "sheriff_campaign")
        self.assertEqual(villager["pending"]["actor"], 1)

        seer = create_game(1, "seer", seed=4)
        engine = HumanGameEngine(BuiltinAIProvider())
        engine.advance_ai(seer)
        self.assertEqual(seer["pending"]["action"], "divine")
        target = seer["pending"]["allowed_targets"][0]
        engine.submit_human(seer, {"action": "divine", "target": target})
        self.assertEqual(seer["phase"], "sheriff_campaign")
        self.assertEqual(seer["pending"]["actor"], 1)

    def test_withdraw_results_publish_together(self):
        state = create_game(1, "villager", seed=7)
        state["sheriff_election_players"] = [1, 2, 3]
        state["sheriff_candidates"] = [1, 2, 3]
        _start_withdraw(state, [1, 2, 3])
        HumanGameEngine(QuietElectionProvider()).submit_human(
            state, {"action": "withdraw", "withdraw": False}
        )
        announcements = [event for event in state["history"]
                         if "退水结果同时公布" in event["text"]]
        self.assertEqual(len(announcements), 1)

    def test_ai_view_does_not_reveal_which_seat_is_human(self):
        state = create_game(7, "villager", seed=3)
        ai = next(seat for seat in range(1, 13) if seat != 7)
        view = get_visible_state(state, ai)
        self.assertFalse(any(player["is_human"] for player in view["players"]))
        self.assertFalse(any(player["profile_id"] for player in view["players"]))
        self.assertNotIn("你", {player["name"] for player in view["players"]})
        self.assertTrue(all(player["avatar"] for player in view["players"]))
        human_view = public_state_for_human(state)
        human_player = next(player for player in human_view["players"] if player["seat"] == 7)
        self.assertTrue(human_player["is_human"])
        self.assertEqual(human_player["name"], "牢雨")
        self.assertEqual(human_player["avatar"], "/assets/avatars/player_12.jpg")

    def test_public_history_never_names_the_human_seat(self):
        state = create_game(7, "villager", seed=3)
        ai = next(seat for seat in range(1, 13) if seat != 7)
        view = get_visible_state(state, ai)
        leaked = [event["text"] for event in view["history"] if "真人" in event["text"]]
        self.assertEqual(leaked, [])

    def test_exiled_player_gets_last_words_before_night(self):
        state = create_game(1, "villager", seed=17)
        _start_day_vote(state)
        state["phase_data"]["votes"] = {str(voter): 1 for voter in range(1, 13)}
        _finish_day_vote(state, revote=False)
        self.assertEqual(state["phase"], "last_words")
        self.assertEqual(state["pending"]["actor"], 1)
        self.assertFalse(state["players"]["1"]["alive"])
        HumanGameEngine(BuiltinAIProvider()).submit_human(
            state, {"action": "last_words", "text": "这是我的遗言，请继续盘清票型。"}
        )
        self.assertNotEqual(state["phase"], "last_words")
        self.assertTrue(any(event["phase"] == "last_words" for event in state["history"]))

    def test_human_completes_sheriff_flow_and_leads_vote(self):
        state = create_game(1, "werewolf", seed=18)
        engine = HumanGameEngine(QuietElectionProvider())

        def advance_until(action):
            for _ in range(300):
                if state["pending"]["actor"] == 1 and state["pending"]["action"] == action:
                    return
                if state["pending"]["actor"] == 1:
                    engine.submit_human(state, human_intent(state))
                else:
                    engine.advance_ai(state)
            self.fail(f"没有到达真人动作 {action}")

        advance_until("campaign")
        engine.submit_human(state, {"action": "campaign", "join": True})
        advance_until("campaign_speech")
        engine.submit_human(state, {"action": "campaign_speech", "text": "我上警带队，先听清每个人的验人和站边。"})
        advance_until("withdraw")
        engine.submit_human(state, {"action": "withdraw", "withdraw": False})
        advance_until("sheriff_order")
        self.assertEqual(state["sheriff"], 1)
        engine.submit_human(state, {"action": "sheriff_order", "direction": "forward"})
        advance_until("speech")
        engine.submit_human(state, {"action": "speech", "text": "听完一圈我先按公开发言和票型归票，不拿身份信息倒推。"})
        advance_until("sheriff_recommend")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "sheriff_recommend", "target": target})
        advance_until("vote")
        self.assertEqual(state["phase"], "day_vote")

    def test_day_vote_tie_enters_pk_and_second_tie_exiles_nobody(self):
        state = create_game(12, "villager", seed=20)
        _start_day_vote(state)
        alive = list(range(1, 13))
        state["phase_data"]["votes"] = {
            str(voter): 1 if voter % 2 else 2 for voter in alive
        }
        _finish_day_vote(state, revote=False)
        self.assertEqual(state["phase"], "day_pk_speech")
        self.assertEqual(state["phase_data"]["tied"], [1, 2])
        state["phase"] = "day_revote"
        state["phase_data"] = {"votes": {str(v): 1 if v % 2 else 2 for v in range(3, 13)}}
        _finish_day_vote(state, revote=True)
        self.assertIn("无人出局", state["history"][-2]["text"])

    def test_sheriff_vote_tie_enters_pk(self):
        state = create_game(12, "villager", seed=22)
        state["phase"] = "sheriff_vote"
        state["phase_data"] = {"votes": {"3": 1, "4": 2}, "candidates": [1, 2]}
        _finish_sheriff_vote(state, revote=False)
        self.assertEqual(state["phase"], "sheriff_pk_speech")
        self.assertEqual(state["phase_data"]["tied"], [1, 2])

    def test_full_offline_game_reaches_a_winner(self):
        state = create_game(7, "villager", seed=4)
        drive(state, limit=1500)
        self.assertIn(state["winner"], {"好人阵营", "狼人阵营"})

    def test_every_seat_speaks_in_postgame_debrief_before_game_over(self):
        state = create_game(1, "villager", seed=31)
        for seat in range(1, 13):
            if state["roles"][str(seat)] in WOLF_ROLES:
                state["players"][str(seat)]["alive"] = False

        _after_deaths(state, "post_exile")

        self.assertEqual(state["winner"], "好人阵营")
        self.assertEqual(state["phase"], "post_game_speech")
        self.assertEqual(state["phase_data"]["queue"], list(range(1, 13)))
        self.assertEqual(state["pending"]["actor"], 1)
        revealed = get_visible_state(state, 2)
        self.assertTrue(all(player["role"] for player in revealed["players"]))

        drive(state, stop=lambda current: current["phase"] == "mvp_vote", limit=30)

        self.assertEqual(state["pending"]["action"], "mvp_vote")
        self.assertIn(state["human_seat"], state["pending"]["allowed_targets"])

        engine = HumanGameEngine(BuiltinAIProvider())
        engine.submit_human(state, human_intent(state))
        self.assertEqual(state["phase"], "mvp_vote")
        self.assertEqual(len(state["phase_data"]["votes"]), 1)
        self.assertNotEqual(state["pending"]["actor"], state["human_seat"])
        engine.advance_ai(state)
        self.assertEqual(len(state["phase_data"]["votes"]), 2)

        drive(state, limit=30)

        debriefs = [event for event in state["history"]
                    if event["kind"] == "speech" and event["phase"] == "post_game_speech"]
        self.assertEqual(state["phase"], "game_over")
        self.assertEqual([event["speaker"] for event in debriefs], list(range(1, 13)))
        self.assertEqual(len(state["mvp_votes"]), 12)
        self.assertEqual(sum(state["mvp_result"]["counts"].values()), 12)
        self.assertTrue(state["mvp_result"]["winners"])
        self.assertIsNone(state["pending"])

    def test_codex_bridge_task_contains_only_visible_state(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        with tempfile.TemporaryDirectory() as directory:
            provider = CodexFileProvider(Path(directory))
            result = provider.request_action(get_visible_state(state, actor), state["pending"])
            self.assertIsNone(result)
            task = json.loads(next((Path(directory) / "tasks").glob("*.json")).read_text(encoding="utf-8"))
            self.assertNotIn("roles", task["visible_state"])
            visible_roles = {p["seat"]: p["role"] for p in task["visible_state"]["players"]}
            known_seats = {seat for seat, role in visible_roles.items() if role is not None}
            wolf_seats = {int(seat) for seat, role in state["roles"].items()
                          if role in WOLF_ROLES}
            self.assertEqual(known_seats, wolf_seats)
            self.assertTrue(all(visible_roles[seat] in WOLF_ROLES for seat in wolf_seats))

    def test_codex_cli_provider_returns_structured_isolated_action(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        view = get_visible_state(state, actor)
        request = state["pending"]

        def fake_run(command, **kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(json.dumps({
                "action": request["action"], "target": None,
                "text": "今晚先观察警上位置。", "join": None,
                "withdraw": None, "use": None, "direction": None,
            }, ensure_ascii=False), encoding="utf-8")
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("game.ai_providers.shutil.which", return_value="codex.cmd"), \
                patch("game.ai_providers.subprocess.run", side_effect=fake_run) as run:
            provider = CodexCLIProvider()
            intent = provider.request_action(view, request)

        self.assertEqual(intent["action"], request["action"])
        sent = run.call_args.kwargs["input"]
        self.assertNotIn('"roles"', sent)
        self.assertIn('"visible_state"', sent)
        self.assertIn("--ephemeral", run.call_args.args[0])
        self.assertIn("--ignore-user-config", run.call_args.args[0])

    def test_invalid_human_action_does_not_corrupt_state(self):
        state = create_game(2, "seer", seed=19)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "divine")
        before = json.dumps(state, sort_keys=True, ensure_ascii=False)
        with self.assertRaises(ValueError):
            HumanGameEngine(BuiltinAIProvider()).submit_human(
                state, {"action": "divine", "target": 99}
            )
        self.assertEqual(json.dumps(state, sort_keys=True, ensure_ascii=False), before)

    def test_legacy_ai_controller_also_filters_hidden_fields(self):
        players = {"p1": Villager("p1", "一号"), "p2": Werewolf("p2", "二号")}
        game_state = {"players": {
            "p1": {"name": "一号", "is_alive": True, "role": "villager", "ai_model": "A"},
            "p2": {"name": "二号", "is_alive": True, "role": "werewolf", "ai_model": "B"},
        }, "alive_count": {"werewolf": 1, "villager": 1}, "current_discussion": [
            {"player": "二号", "role": "werewolf", "content": "公开发言"}], "history": [
            {"round": 1, "phase": "night", "event": "seer_check", "target": "p2", "is_wolf": True},
            {"round": 1, "phase": "discussion", "player": "p2", "content": "公开发言", "target_role": "werewolf"},
        ]}
        view = get_legacy_visible_state(game_state, players, "p1")
        self.assertNotIn("role", view["players"]["p2"])
        self.assertNotIn("alive_count", view)
        self.assertNotIn("role", view["current_discussion"][0])
        self.assertNotIn("target_role", view["history"][0])
        self.assertFalse(any(item.get("event") == "seer_check" for item in view["history"]))


if __name__ == "__main__":
    unittest.main()
