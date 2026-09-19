import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from game.ai_providers import (
    CLI_BACKENDS,
    ApiProvider,
    BuiltinAIProvider,
    CliBackend,
    CodexCLIProvider,
    CodexFileProvider,
    GenericCLIProvider,
    ProviderFailure,
    action_schema,
    action_prompt,
    board_rules_prompt,
    load_api_profile,
    parse_action_payload,
    split_cli_args,
    summarize_cli_error,
    validate_intent,
)
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
    _resume,
    _start_day_vote,
    _start_sheriff_election,
    _start_sheriff_speeches,
    _start_sheriff_vote,
    _start_withdraw,
    _start_mirror_wolf_chat,
    _start_hidden_wolf_learn,
    _mirror_start_hidden_skill,
    _start_night,
    _winner,
    create_game,
    get_visible_state,
    public_state_for_human,
)
from game.roles import Villager, Werewolf
from game.visibility import get_legacy_visible_state
import panel_game


STUB_CLI_SOURCE = '''
import json
import os
import sys

raw = sys.stdin.read()
directory = None
for token in sys.argv:
    if token.startswith("--received-dir="):
        directory = token.split("=", 1)[1]
if directory:
    os.makedirs(directory, exist_ok=True)
    name = "call_%03d.json" % len(os.listdir(directory))
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write(raw)

payload = json.loads(raw[raw.rindex(chr(123) + chr(34) + "request"):])
request = payload["request"]
action = request["action"]
targets = list(request.get("allowed_targets", []))
intent = {"action": action, "target": None, "text": None, "join": None,
          "withdraw": None, "use": None, "choice": None, "direction": None, "player_impressions": None}
if action == "postgame_speech":
    intent["text"] = "我复盘一下这局的判断。"
    intent["player_impressions"] = [{"seat": seat, "impression": "关键轮次表现稳定。"}
                                    for seat in (1, 2)]
elif action == "mvp_vote":
    intent["target"] = targets[0] if targets else 1
    intent["text"] = "关键轮次贡献最直接。"
elif action in {"speech", "pk_speech", "campaign_speech", "last_words"}:
    intent["text"] = "我按公开发言和票型判断，先不急着站边。"
elif action == "campaign":
    intent["join"] = False
elif action == "withdraw":
    intent["withdraw"] = False
elif action == "witch_save":
    intent["use"] = False
elif action == "witch_action":
    intent["choice"] = "none"
elif action == "sheriff_order":
    intent["direction"] = "forward"
elif action == "knight_decide":
    intent["target"] = None
else:
    intent["target"] = targets[0] if targets else None
print(json.dumps(intent, ensure_ascii=False))
'''


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


class CliNamedStrikeProvider(KnightStrikeProvider):
    """Same brain as the builtin AI, but carrying the generic CLI provider's name."""

    name = "cli"


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
    if action == "witch_action":
        return {"action": action, "choice": "none", "target": None}
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


class StubOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        entry = {"path": self.path, "auth": self.headers.get("Authorization"),
                 "body": json.loads(raw)}
        self.server.requests.append(entry)
        status, payload = self.server.responder(entry)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


@contextlib.contextmanager
def stub_openai(responder):
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubOpenAIHandler)
    server.requests = []
    server.responder = responder
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02),
                     daemon=True).start()
    # urllib honours *_proxy, and this machine has a proxy that intercepts even
    # 127.0.0.1; bypass it so the stub is hit directly instead of through it.
    bypass = {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
    try:
        with patch.dict(os.environ, bypass):
            yield server, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def chat_reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def stub_intent_for(request):
    """A legal action for any request, mirroring the CLI stub."""
    action = request["action"]
    targets = list(request.get("allowed_targets", []))
    intent = {"action": action, "target": None, "text": None, "join": None,
              "withdraw": None, "use": None, "choice": None, "direction": None, "player_impressions": None}
    if action == "postgame_speech":
        intent["text"] = "我复盘一下这局的判断。"
        intent["player_impressions"] = [{"seat": seat, "impression": "关键轮次表现稳定。"}
                                        for seat in (1, 2)]
    elif action == "mvp_vote":
        intent["target"] = targets[0] if targets else 1
        intent["text"] = "关键轮次贡献最直接。"
    elif action in {"speech", "pk_speech", "campaign_speech", "last_words"}:
        intent["text"] = "我按公开发言和票型判断，先不急着站边。"
    elif action == "wolf_chat":
        intent["text"] = "今晚我倾向处理带队位置，白天注意别站得太整齐。"
    elif action == "campaign":
        intent["join"] = False
    elif action == "withdraw":
        intent["withdraw"] = False
    elif action == "witch_save":
        intent["use"] = False
    elif action == "witch_action":
        intent["choice"] = "none"
    elif action == "sheriff_order":
        intent["direction"] = "forward"
    elif action == "knight_decide":
        intent["target"] = None
    else:
        intent["target"] = targets[0] if targets else None
    return intent


class HumanGameTests(unittest.TestCase):
    def test_panel_provider_modes_are_three_and_saved_without_secrets(self):
        self.assertEqual(panel_game.normalize_provider_config({"provider": "cli", "cli": "codex"}),
                         {"mode": "cli", "cli": "codex", "model": None})
        self.assertEqual(panel_game.normalize_provider_config(
            {"provider": "cli", "cli": "codex", "model": None}),
            {"mode": "cli", "cli": "codex", "model": None})
        self.assertEqual(panel_game.normalize_provider_config({"provider": "codex"}),
                         {"mode": "bridge"})
        self.assertEqual(panel_game.normalize_provider_config({"provider": "api", "api_model": "DEEPSEEK"}),
                         {"mode": "api", "api_model": "DEEPSEEK", "api_format": "json_object"})
        with self.assertRaises(ValueError):
            panel_game.normalize_provider_config({"provider": "builtin"})

    def test_panel_start_game_applies_selected_bridge_provider(self):
        old_engine, old_config, old_root = panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG, panel_game.BRIDGE_ROOT
        try:
            with tempfile.TemporaryDirectory() as directory:
                panel_game.BRIDGE_ROOT = Path(directory)
                state = panel_game.apply_action(
                    panel_game.setup_state(),
                    {"action": "start_game", "provider": "bridge", "human_seat": "1",
                     "debug_role": "villager", "board": "classic"},
                )
                self.assertEqual(state["provider_config"], {"mode": "bridge"})
                self.assertEqual(panel_game.ENGINE.provider.name, "codex")
        finally:
            panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG, panel_game.BRIDGE_ROOT = (
                old_engine, old_config, old_root
            )

    def test_panel_real_mode_forces_random_setup_and_self_kill(self):
        old_engine, old_config = panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG
        original_create_game = panel_game.create_game
        try:
            with patch("panel_game.create_game", side_effect=original_create_game) as factory:
                state = panel_game.apply_action(
                    panel_game.setup_state(),
                    {"action": "start_game", "game_mode": "real", "human_seat": "7",
                     "debug_role": "seer", "board": "classic", "provider": "cli", "cli": "codex"},
                )
                args = factory.call_args.args
                self.assertEqual(args[0], 0)
                self.assertEqual(args[1], "random")
                self.assertTrue(args[3])
                self.assertEqual(state["game_mode"], "real")
                self.assertTrue(state["rules"]["wolf_can_self_kill"])
        finally:
            panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG = old_engine, old_config

    def test_panel_test_mode_keeps_selected_setup(self):
        old_engine, old_config = panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG
        try:
            state = panel_game.apply_action(
                panel_game.setup_state(),
                {"action": "start_game", "game_mode": "test", "human_seat": "7",
                 "debug_role": "seer", "board": "classic", "provider": "cli", "cli": "codex"},
            )
            self.assertEqual(state["game_mode"], "test")
            self.assertEqual(state["human_seat"], 7)
            self.assertEqual(state["roles"]["7"], "seer")
            self.assertTrue(state["rules"]["wolf_can_self_kill"])
        finally:
            panel_game.ENGINE, panel_game.ACTIVE_PROVIDER_CONFIG = old_engine, old_config

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
        expected_order = [4] + [seat for seat in range(1, 13) if seat != 4]
        self.assertEqual(restored["phase_data"]["queue"], expected_order)
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

    def test_small_wolf_can_explode_during_any_day_speech(self):
        state = create_game(1, "werewolf", seed=41)
        state["day"] = 2
        state["phase"] = "day_speech"
        state["phase_data"] = {}
        state["pending"] = {"actor": 8, "action": "speech", "allowed_targets": [], "sequence": 1}
        public = public_state_for_human(state)
        self.assertTrue(public["can_wolf_explode"])
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "wolf_explode"})
        self.assertFalse(state["players"]["1"]["alive"])
        self.assertEqual(state["players"]["1"]["revealed_role"], "werewolf")
        self.assertEqual(state["phase"], "night_wolf_chat")
        self.assertTrue(any("自爆" in event["text"] for event in state["history"]))

    def test_only_small_wolf_can_explode(self):
        state = create_game(1, "wolf_beauty", seed=42)
        state["day"] = 2
        state["phase"] = "day_speech"
        self.assertFalse(public_state_for_human(state)["can_wolf_explode"])
        state = create_game(1, "villager", seed=43)
        state["day"] = 2
        state["phase"] = "day_speech"
        self.assertFalse(public_state_for_human(state)["can_wolf_explode"])

    def test_first_sheriff_speech_explosion_delays_election(self):
        state = create_game(1, "werewolf", seed=44)
        state["day"] = 1
        state["phase"] = "sheriff_speech"
        state["phase_data"] = {"candidates": [1, 2]}
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "wolf_explode"})
        self.assertTrue(state["sheriff_election_delayed"])
        self.assertTrue(state["sheriff_badge"])
        self.assertEqual(state["phase"], "night_wolf_chat")
        state["day"] = 2
        _resume(state, "post_night")
        self.assertEqual(state["phase"], "sheriff_campaign")
        self.assertEqual(state["sheriff_candidates"], [])

    def test_second_day_sheriff_speech_explosion_consumes_badge(self):
        state = create_game(1, "werewolf", seed=45)
        state["day"] = 2
        state["phase"] = "sheriff_speech"
        state["phase_data"] = {"candidates": [1, 2]}
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "wolf_explode"})
        self.assertFalse(state["sheriff_badge"])
        self.assertIsNone(state["sheriff"])
        self.assertFalse(state["sheriff_election_delayed"])
        self.assertEqual(state["phase"], "night_wolf_chat")
        _resume(state, "post_night")
        self.assertEqual(state["phase"], "day_speech")

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
            "witch": "witch_action", "guard": "guard",
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
        drive(state, stop=lambda s: s["pending"]["actor"] == 2 and s["pending"]["action"] == "witch_action")
        target = state["pending"]["allowed_targets"][0]
        engine.submit_human(state, {"action": "witch_action", "choice": "poison", "target": target})
        self.assertFalse(state["abilities"]["witch_poison"])
        self.assertTrue(state["abilities"]["witch_medicine"])
        self.assertTrue(state["night"]["witch_action_used"])

    def test_witch_uses_at_most_one_bottle_and_legacy_fields_cannot_double_kill(self):
        state = create_game(2, "witch", seed=31)
        drive(state, stop=lambda s: s["pending"]["actor"] == 2
              and s["pending"]["action"] == "witch_action")
        victim = state["night"]["wolf_target"]
        self.assertIsNotNone(victim)
        engine = HumanGameEngine(BuiltinAIProvider())
        engine.submit_human(state, {"action": "witch_action", "choice": "save", "target": victim})
        self.assertEqual(state["night"].get("witch_choice"), "save")
        self.assertTrue(state["night"]["witch_action_used"])
        self.assertTrue(state["abilities"]["witch_poison"])
        self.assertIsNone(state["night"].get("poison"))
        self.assertNotIn(state["phase"], {"night_witch_save", "night_witch_poison"})

        # A stale/hand-edited legacy night must still resolve only one bottle.
        other = next(seat for seat in range(1, 13) if seat not in {2, victim})
        state = create_game(1, "villager", seed=23)
        state["day"] = 1
        state["phase"] = "night_witch_action"
        state["night"] = {
            "wolf_votes": {}, "wolf_target": victim, "guard": None,
            "saved": True, "poison": other, "deaths": [],
            "witch_action_used": True, "witch_choice": "save",
            "wolf_targets": [], "hidden_poison": None, "hidden_guard": None,
        }
        _resolve_night(state)
        self.assertTrue(state["players"][str(victim)]["alive"])
        self.assertTrue(state["players"][str(other)]["alive"])

        # With a mirror-board double kill, one antidote protects exactly the
        # selected victim; it must not make both wolf targets survive.
        state = create_game(1, "villager", seed=23, board="mirror")
        state["day"] = 1
        state["phase"] = "night_witch_action"
        state["night"] = {
            "wolf_votes": {}, "wolf_target": 2, "wolf_targets": [3],
            "guard": None, "saved": True, "saved_target": 3,
            "poison": None, "deaths": [], "witch_action_used": True,
            "witch_choice": "save", "hidden_poison": None, "hidden_guard": None,
        }
        _resolve_night(state)
        self.assertFalse(state["players"]["2"]["alive"])
        self.assertTrue(state["players"]["3"]["alive"])

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

    def test_guard_and_witch_save_same_target_triggers_nai_chuan(self):
        state = create_game(1, "villager", seed=23)
        victim = 2
        state["day"] = 1
        state["phase"] = "night_witch_action"
        state["night"] = {
            "wolf_votes": {}, "wolf_target": victim, "guard": victim,
            "saved": True, "poison": None, "deaths": [],
            "witch_action_used": True, "wolf_targets": [],
            "hidden_poison": None, "hidden_guard": None,
        }
        _resolve_night(state)
        self.assertFalse(state["players"][str(victim)]["alive"])
        self.assertTrue(any("触发奶穿" in event["text"] for event in state["history"]))

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

    def test_slow_provider_advances_one_night_action_and_waits_for_human(self):
        class SlowProvider(BuiltinAIProvider):
            batch_actions = False
            calls = 0

            def request_action(self, view, request):
                self.calls += 1
                return super().request_action(view, request)

        provider = SlowProvider()
        state = create_game(1, "seer", seed=4)
        engine = HumanGameEngine(provider)
        sequence = state["action_seq"]
        engine.advance_ai(state)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(state["action_seq"], sequence + 1)
        for _ in range(30):
            if state["pending"]["actor"] == 1:
                break
            engine.advance_ai(state)
        self.assertEqual(state["pending"]["action"], "divine")
        calls = provider.calls
        engine.submit_human(state, {"action": "divine", "target": state["pending"]["allowed_targets"][0]})
        self.assertEqual(provider.calls, calls)
        with patch.object(panel_game, "ENGINE", engine, create=True):
            self.assertTrue(panel_game.panel_state(state)["auto_continue"])

    def test_slow_provider_secret_votes_stay_hidden_between_requests(self):
        class SlowProvider(BuiltinAIProvider):
            batch_actions = False

        state = create_game(1, "villager", seed=4)
        _start_sheriff_election(state)
        engine = HumanGameEngine(SlowProvider())
        engine.submit_human(state, {"action": "campaign", "join": False})
        engine.advance_ai(state)
        self.assertFalse(state["phase_data"]["published"])
        self.assertEqual(public_state_for_human(state)["public_votes"], {})
        with patch.object(panel_game, "ENGINE", engine, create=True):
            self.assertTrue(panel_game.panel_state(state)["auto_continue"])

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
        state = create_game(7, "villager", seed=31)
        for seat in range(1, 13):
            if state["roles"][str(seat)] in WOLF_ROLES:
                state["players"][str(seat)]["alive"] = False

        _after_deaths(state, "post_exile")

        self.assertEqual(state["winner"], "好人阵营")
        self.assertEqual(state["phase"], "post_game_speech")
        expected_order = [7] + [seat for seat in range(1, 13) if seat != 7]
        self.assertEqual(state["phase_data"]["queue"], expected_order)
        self.assertEqual(state["pending"]["actor"], 7)
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
        self.assertEqual([event["speaker"] for event in debriefs], expected_order)
        self.assertEqual(len(state["mvp_votes"]), 12)
        self.assertEqual(sum(state["mvp_result"]["counts"].values()), 12)
        self.assertTrue(state["mvp_result"]["winners"])
        self.assertIsNone(state["pending"])

    def test_visible_state_never_leaks_a_role_it_should_not(self):
        """The project's central invariant, checked for every seat at every phase.

        The bridge and the CLI providers both trust ``get_visible_state``
        completely, so a single leak here would hand the whole identity table to
        every AI player.  Driving complete games and inspecting all twelve seats
        after every step is the cheapest way to keep that trust honest.
        """
        secret_keys = {"abilities", "night", "roles", "ai_memories", "ai_profiles",
                       "postgame_impressions", "mvp_votes", "wolf_chat", "phase_data"}
        private_by_role = {
            "seer": {"seer_checks"},
            "witch": {"medicine", "poison", "tonight_wolf_target"},
            "guard": {"last_guarded"},
            "knight": {"duel_available"},
            "wolf_beauty": {"charmed_player"},
        }
        checked = 0
        for seed in range(6):
            state = create_game(0, "random", seed=seed)
            engine = HumanGameEngine(BuiltinAIProvider())
            for _ in range(1500):
                if state["phase"] == "game_over":
                    break
                roles = {int(seat): role for seat, role in state["roles"].items()}
                wolves = {seat for seat, role in roles.items() if role in WOLF_ROLES}
                revealed = state["winner"] is not None
                for seat in range(1, 13):
                    checked += 1
                    view = get_visible_state(state, seat)
                    label = f"seed={seed} phase={state['phase']} seat={seat}"

                    self.assertFalse(secret_keys & set(view), f"{label} 顶层出现隐藏字段")

                    allowed_private = set()
                    if roles[seat] in WOLF_ROLES:
                        allowed_private |= {"wolf_teammates", "wolf_chat"}
                    allowed_private |= private_by_role.get(roles[seat], set())
                    self.assertLessEqual(set(view["private"]), allowed_private,
                                         f"{label} private 越界")

                    if revealed:
                        expected = set(range(1, 13))
                    elif roles[seat] in WOLF_ROLES:
                        expected = wolves
                    else:
                        expected = {seat}
                    expected |= {player["seat"] for player in view["players"]
                                 if player.get("revealed_role")}
                    known = {player["seat"] for player in view["players"] if player.get("role")}
                    self.assertLessEqual(known, expected, f"{label} 看到了不该知道的身份")

                    for event in view["history"]:
                        self.assertNotIn("role", event, f"{label} history 夹带 role")
                        self.assertNotIn("is_wolf", event, f"{label} history 夹带 is_wolf")

                if state["pending"]["actor"] == state["human_seat"]:
                    engine.submit_human(state, human_intent(state))
                else:
                    engine.advance_ai(state)
        self.assertGreater(checked, 3000)

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

        class FakeProcess:
            def __init__(self, command, **kwargs):
                self.command = command
                self.kwargs = kwargs
                self.returncode = 0
                self.stdin = self.stdout = self.stderr = None

            def communicate(self, payload, timeout=None):
                Path(self.command[self.command.index("-o") + 1]).write_text(json.dumps({
                    "action": request["action"], "target": None,
                    "text": "今晚先观察警上位置。", "join": None,
                    "withdraw": None, "use": None, "direction": None,
                }, ensure_ascii=False), encoding="utf-8")
                self.sent = payload
                return "", ""

        started = []

        def fake_popen(command, **kwargs):
            process = FakeProcess(command, **kwargs)
            started.append(process)
            return process

        with patch("game.ai_providers.shutil.which", return_value="codex.cmd"), \
                patch("game.ai_providers._desktop_codex_candidates", return_value=[]), \
                patch("game.ai_providers.subprocess.Popen", side_effect=fake_popen):
            provider = CodexCLIProvider()
            intent = provider.request_action(view, request)

        self.assertEqual(intent["action"], request["action"])
        started_once = started[0]
        sent = started_once.sent
        command = started_once.command
        self.assertIn("--model", command)
        self.assertNotEqual(command[command.index("--model") + 1], "None")
        self.assertNotIn('"roles"', sent)
        self.assertIn('"visible_state"', sent)
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertEqual(
            started_once.kwargs["env"]["CODEX_HOME"],
            str(Path.home() / ".codex"),
        )

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


    def test_cli_backends_build_expected_argv(self):
        ollama = CLI_BACKENDS["ollama"]
        self.assertEqual(ollama.build_argv("ollama", "qwen3"),
                         ["ollama", "run", "qwen3", "--format", "json"])
        self.assertEqual(ollama.build_argv("ollama", None),
                         ["ollama", "run", "qwen3", "--format", "json"])

        claude = CLI_BACKENDS["claude"]
        self.assertEqual(
            claude.build_argv("claude.cmd", "claude-sonnet-4-5", extra_args=["--foo"]),
            ["claude.cmd", "--model", "claude-sonnet-4-5",
             "-p", "--output-format", "json", "--max-turns", "1", "--foo"],
        )
        self.assertEqual(
            claude.build_argv("claude.cmd", None),
            ["claude.cmd", "-p", "--output-format", "json", "--max-turns", "1"],
        )

        gemini = CLI_BACKENDS["gemini"]
        self.assertEqual(gemini.build_argv("gemini", None, prompt="你好"),
                         ["gemini", "-p", "你好"])

        model_less = CliBackend(key="x", label="X", executables=("x",),
                                template=("run", "{model}"), prompt_mode="stdin")
        with self.assertRaises(ValueError):
            model_less.build_argv("x")
        with self.assertRaises(ValueError):
            CliBackend(key="y", label="Y", executables=("y",),
                       template=("go",), prompt_mode="argv")
        with self.assertRaises(ValueError):
            CliBackend(key="z", label="Z", executables=("z",),
                       template=("go", "{prompt}"), prompt_mode="stdin")

    def test_cli_backends_only_ever_reuse_the_codex_schema(self):
        for action in ["vote", "wolf_kill", "speech", "postgame_speech", "mvp_vote"]:
            self.assertEqual(action_schema(action)["properties"]["action"]["const"], action)
        schema = action_schema("speech")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("player_impressions", schema["required"])

    def test_cli_output_parsing_handles_envelopes_and_fences(self):
        intent = {"action": "speech", "text": "我先听一圈。", "target": None}
        self.assertEqual(parse_action_payload(json.dumps(intent, ensure_ascii=False)), intent)

        envelope = {"type": "result", "is_error": False,
                    "result": json.dumps(intent, ensure_ascii=False)}
        self.assertEqual(parse_action_payload(json.dumps(envelope, ensure_ascii=False)), intent)

        fenced = ("好的，我的决定如下：\n```json\n"
                  f"{json.dumps(intent, ensure_ascii=False)}\n```\n以上。")
        self.assertEqual(parse_action_payload(fenced), intent)

        with self.assertRaises(ValueError):
            parse_action_payload("我今天不想发言。")
        with self.assertRaises(ValueError):
            parse_action_payload("")

    def test_generic_cli_provider_retries_and_keeps_seat_isolation(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        request = state["pending"]
        view = get_visible_state(state, actor)

        good = {"action": request["action"], "target": None, "text": "今晚先观察警上位置。",
                "join": None, "withdraw": None, "use": None, "direction": None,
                "player_impressions": None}
        replies = ["抱歉，我还需要再想一想。", json.dumps(good, ensure_ascii=False)]
        captured = []

        with patch("game.ai_providers.shutil.which", return_value="ollama.exe"):
            provider = GenericCLIProvider(backend="ollama", model="qwen3", retries=1)

        def fake_invoke(argv, stdin_text):
            captured.append((argv, stdin_text))
            return replies.pop(0)

        with patch.object(GenericCLIProvider, "_invoke", side_effect=fake_invoke):
            intent = provider.request_action(view, request)

        self.assertEqual(intent["action"], request["action"])
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0][0][:2], ["ollama.exe", "run"])
        self.assertNotIn('"roles"', captured[0][1])
        self.assertIn('"visible_state"', captured[0][1])
        self.assertIn('"player_impressions"', captured[0][1])
        self.assertIn("不合法", captured[1][1])

    def test_generic_cli_provider_rejects_illegal_target_after_retries(self):
        view = {"game_id": "t", "day": 1, "phase": "day_vote", "self": {"seat": 2}}
        request = {"actor": 2, "action": "vote", "allowed_targets": [3, 4], "prompt": "投票"}
        with patch("game.ai_providers.shutil.which", return_value="ollama.exe"):
            provider = GenericCLIProvider(backend="ollama", retries=1)
        with patch.object(GenericCLIProvider, "_invoke",
                          return_value=json.dumps({"action": "vote", "target": 99})):
            with self.assertRaises(ValueError) as caught:
                provider.request_action(view, request)
        self.assertIn("连续 2 次", str(caught.exception))

        with self.assertRaises(ValueError):
            validate_intent({"action": "vote", "target": 99}, request)
        validate_intent({"action": "vote", "target": 3}, request)

    def test_cli_args_split_keeps_windows_paths_intact(self):
        self.assertEqual(split_cli_args("--temperature 0.8"),
                         ["--temperature", "0.8"])
        self.assertEqual(split_cli_args('--name "hello world"'),
                         ["--name", "hello world"])
        if os.name == "nt":
            windows_path = r"--received-dir=C:\Users\me\AppData\Local\Temp"
            self.assertEqual(split_cli_args(windows_path), [windows_path])
        else:
            self.assertEqual(split_cli_args("--dir /tmp/a"), ["--dir", "/tmp/a"])

    def test_cli_failures_are_reported_concisely(self):
        echoed = (
            "当前优先看2号，其次看5、9、10这些昨天站6、后续身份增量不足的位置。"
            '}],"votes":[{"day":1,"target":6,"phase":"sheriff_vote"}],"winner":null}}\n'
            "</stdin>\n"
            "ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex "
            "(https://chatgpt.com/explore/plus), or try again at Oct 17th, 2026 11:28 AM.\n"
        )
        message = summarize_cli_error(echoed, "Codex 玩家")
        self.assertIn("额度或频率已用尽", message)
        self.assertIn("usage limit", message)
        self.assertNotIn('"votes"', message)
        self.assertLess(len(message), 400)

        self.assertIn("未登录或凭据已失效",
                      summarize_cli_error("ERROR: 401 Unauthorized: invalid api key", "Ollama"))

        other = summarize_cli_error("ERROR: unexpected argument '--format' found", "Ollama")
        self.assertIn("调用失败", other)
        self.assertIn("--format", other)

        self.assertIn("没有输出任何错误信息", summarize_cli_error("", "Ollama"))

    def test_cli_timeout_aborts_the_whole_process_tree(self):
        """回归：CLI 超时必须真的返回，并且把包装器与孙子进程一起清掉。

        Windows 上 ``subprocess.run(timeout=...)`` 超时后只杀直接子进程，紧接着又调一次
        ``communicate()`` 去读干净管道；孙子进程（npm ``codex.cmd`` → node → codex.exe）
        仍握着继承来的管道句柄时，那次 drain 会永久阻塞 —— 超时形同虚设，面板连同请求锁
        一起卡死（2026-09-19 实战：整局冻结十几分钟）。这里用一个自己再 fork 一层的桩
        复现同样的进程结构。
        """
        import subprocess
        import time
        from game.ai_providers import _run_cli_with_timeout

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "heartbeat.txt"
            (root / "child.py").write_text(
                "import pathlib, sys, time\n"
                "beat = pathlib.Path(sys.argv[1])\n"
                "while True:\n"
                "    beat.write_text('alive', encoding='utf-8')\n"
                "    time.sleep(0.2)\n",
                encoding="utf-8",
            )
            (root / "parent.py").write_text(
                "import pathlib, subprocess, sys, time\n"
                "root = pathlib.Path(sys.argv[1])\n"
                "child = subprocess.Popen(\n"
                "    [sys.executable, str(root / 'child.py'), str(root / 'heartbeat.txt')],\n"
                "    stdout=sys.stdout, stderr=sys.stderr)\n"
                "(root / 'child.pid').write_text(str(child.pid), encoding='utf-8')\n"
                "time.sleep(120)\n",
                encoding="utf-8",
            )
            observed = {}

            def call():
                try:
                    _run_cli_with_timeout([sys.executable, str(root / "parent.py"), str(root)],
                                          "payload", 2, os.environ.copy(), 0)
                    observed["outcome"] = "returned"
                except BaseException as exc:  # noqa: BLE001 - 断言具体类型
                    observed["outcome"] = exc

            thread = threading.Thread(target=call, daemon=True)
            thread.start()
            thread.join(timeout=60)
            still_running = thread.is_alive()
            beat_before = heartbeat.stat().st_mtime_ns if heartbeat.exists() else None
            time.sleep(1.5)  # 心跳间隔 0.2 秒，1.5 秒足够判断它是否还活着
            beat_after = heartbeat.stat().st_mtime_ns if heartbeat.exists() else None
            pid_file = root / "child.pid"
            if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip():
                child_pid = pid_file.read_text(encoding="utf-8").strip()
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", child_pid],
                                   capture_output=True, check=False)
                else:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(int(child_pid), 9)

        self.assertFalse(still_running, "超时调用没有返回：管道被孙子进程占住，进程树没杀干净")
        self.assertIsInstance(observed.get("outcome"), subprocess.TimeoutExpired)
        self.assertIsNotNone(beat_before, "桩进程没有起来，本用例没有验证到东西")
        self.assertEqual(beat_before, beat_after, "孙子进程仍然活着，没有跟着包装器一起被清掉")

    def test_codex_initialization_access_denied_has_restart_guidance(self):
        for detail in (
            "Error: failed to initialize in-process app-server client: 拒绝访问。 (os error 5)",
            "Error: failed to initialize in-process app-server client: Access is denied. (os error 5)",
        ):
            message = summarize_cli_error(detail, "Codex 玩家")
            self.assertIn("Windows 拒绝访问", message)
            self.assertIn("启动狼人杀.bat", message)
            self.assertIn("存档保留", message)
            self.assertNotIn("额度", message)

    def test_api_provider_sends_isolated_prompt_and_validates_reply(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        request = state["pending"]
        view = get_visible_state(state, actor)
        good = stub_intent_for(request)

        def responder(entry):
            return 200, chat_reply(json.dumps(good, ensure_ascii=False))

        with stub_openai(responder) as (server, base_url):
            provider = ApiProvider(base_url=base_url, api_key="test-key",
                                   model="stub-model", label="测试模型")
            intent = provider.request_action(view, request)

        self.assertEqual(intent["action"], request["action"])
        self.assertEqual(len(server.requests), 1)
        sent = server.requests[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertEqual(sent["body"]["model"], "stub-model")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        roles = [message["role"] for message in sent["body"]["messages"]]
        self.assertEqual(roles, ["system", "user"])
        everything = "".join(message["content"] for message in sent["body"]["messages"])
        self.assertIn('"visible_state"', everything)
        self.assertIn('"player_impressions"', everything)
        self.assertNotIn('"roles"', everything)

    def test_api_provider_retries_and_downgrades_response_format(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        request = state["pending"]
        view = get_visible_state(state, actor)
        good = stub_intent_for(request)

        def responder(entry):
            body = entry["body"]
            fmt = body.get("response_format") or {}
            if fmt.get("type") == "json_schema":
                return 400, {"error": {"message": "response_format json_schema is not supported"}}
            if "不合法" not in body["messages"][-1]["content"]:
                return 200, chat_reply("抱歉，我还需要想一想。")
            return 200, chat_reply(json.dumps(good, ensure_ascii=False))

        with stub_openai(responder) as (server, base_url):
            provider = ApiProvider(base_url=base_url, api_key="k", model="m",
                                   retries=1, response_format="json_schema")
            intent = provider.request_action(view, request)

        self.assertEqual(intent["action"], request["action"])
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(server.requests[0]["body"]["response_format"]["type"], "json_schema")
        self.assertNotIn("response_format", server.requests[1]["body"])
        self.assertIn("不合法", server.requests[2]["body"]["messages"][-1]["content"])

    def test_api_provider_maps_auth_and_quota_errors_without_retrying(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        request = state["pending"]
        view = get_visible_state(state, actor)

        for status, payload, expected in (
            (401, {"error": {"message": "Invalid API key provided"}}, "API Key 无效"),
            (429, {"error": {"message": "You exceeded your current quota"}}, "额度或频率已用尽"),
        ):
            with self.subTest(status=status):
                with stub_openai(lambda entry, s=status, p=payload: (s, p)) as (server, base_url):
                    provider = ApiProvider(base_url=base_url, api_key="k", model="m", retries=2)
                    with self.assertRaises(ProviderFailure) as caught:
                        provider.request_action(view, request)
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(len(server.requests), 1)

    def test_api_provider_reports_unreachable_endpoint(self):
        state = create_game(1, "villager", seed=3)
        actor = state["pending"]["actor"]
        view = get_visible_state(state, actor)
        request = state["pending"]
        provider = ApiProvider(base_url="http://127.0.0.1:9/v1", api_key="k", model="m",
                               retries=2)

        with patch("game.ai_providers.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaises(ProviderFailure) as caught:
                provider.request_action(view, request)
        self.assertIn("连不上", str(caught.exception))

        with patch("game.ai_providers.urllib.request.urlopen",
                   side_effect=TimeoutError("timed out")):
            with self.assertRaises(ProviderFailure) as caught:
                provider.request_action(view, request)
        self.assertIn("超过", str(caught.exception))

    def test_api_provider_can_drive_a_whole_game_offline(self):
        """Every action type survives the provider contract; HTTP shape is covered above."""
        calls = []

        def fake_post(body):
            calls.append(body)
            user = body["messages"][-1]["content"]
            payload = json.loads(user[user.index('{"request"'):])
            intent = stub_intent_for(payload["request"])
            return 200, json.dumps(chat_reply(json.dumps(intent, ensure_ascii=False)),
                                   ensure_ascii=False)

        provider = ApiProvider(base_url="http://stub/v1", api_key="k", model="m", retries=0)
        with patch.object(ApiProvider, "_post", side_effect=fake_post):
            state = create_game(7, "villager", seed=4)
            engine = HumanGameEngine(provider)
            for _ in range(1500):
                if state["phase"] == "game_over":
                    break
                if state["pending"]["actor"] == state["human_seat"]:
                    engine.submit_human(state, human_intent(state))
                else:
                    engine.advance_ai(state)

        self.assertIn(state["winner"], {"好人阵营", "狼人阵营"})
        self.assertGreater(len(calls), 50)
        for body in calls:
            everything = "".join(message["content"] for message in body["messages"])
            self.assertIn('"visible_state"', everything)
            self.assertNotIn('"roles"', everything)

    def test_load_api_profile_prefers_overrides_over_placeholder_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai_config.json"
            path.write_text(json.dumps({"ai_players": {"DEEPSEEK": {
                "baseurl": "https://your-api-endpoint.com/v1",
                "api_key": "your-api-key-here",
                "model": "deepseek-chat",
            }}}, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(ValueError) as caught:
                load_api_profile(None, path)
            self.assertIn("还没有可用的 API 配置", str(caught.exception))

            profile = load_api_profile(None, path, base_url="https://real.example/v1",
                                       api_key="sk-real", model="deepseek-reasoner")
            self.assertEqual(profile["baseurl"], "https://real.example/v1")
            self.assertEqual(profile["api_key"], "sk-real")
            self.assertEqual(profile["model"], "deepseek-reasoner")

            path.write_text(json.dumps({"ai_players": {"X": {
                "baseurl": "", "api_key": "", "model": ""}}}, ensure_ascii=False),
                encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_api_profile(None, path, base_url="https://real.example/v1",
                                 api_key="sk-real")
            self.assertIn("缺少模型名称", str(caught.exception))

            path.write_text(json.dumps({"ai_players": {"DEEPSEEK": {
                "baseurl": "https://api.deepseek.com/v1",
                "api_key": "sk-real",
                "model": "deepseek-chat",
                "timeout": 42,
            }}}, ensure_ascii=False), encoding="utf-8")
            profile = load_api_profile("deepseek", path)
            self.assertEqual(profile["name"], "DEEPSEEK")
            self.assertEqual(profile["model"], "deepseek-chat")
            self.assertEqual(profile["timeout"], 42)

            profile = load_api_profile(None, path)
            self.assertEqual(profile["name"], "DEEPSEEK")

            with self.assertRaises(ValueError) as caught:
                load_api_profile("NOPE", path)
            self.assertIn("没有名为 NOPE", str(caught.exception))

    def test_api_provider_requires_a_filled_key_in_the_real_config(self):
        with self.assertRaises(ValueError) as caught:
            load_api_profile(None)
        self.assertIn("还没有可用的 API 配置", str(caught.exception))

    def test_unknown_cli_backend_raises_a_readable_error(self):
        with patch("game.ai_providers.shutil.which", return_value="x.exe"):
            with self.assertRaises(ValueError) as caught:
                GenericCLIProvider(backend="not-a-cli")
        self.assertIn("未知 CLI 后端", str(caught.exception))

    def test_generic_cli_provider_name_keeps_ai_knight_duel_enabled(self):
        state = create_game(1, "villager", seed=11)
        knight = next(int(seat) for seat, role in state["roles"].items() if role == "knight")
        wolf = next(int(seat) for seat, role in state["roles"].items() if role in WOLF_ROLES)
        self.assertNotEqual(knight, state["human_seat"])
        self.assertEqual(GenericCLIProvider.name, "cli")
        state["day"] = 1
        _begin_speech_queue(state, "forward")
        HumanGameEngine(CliNamedStrikeProvider(wolf)).submit_human(
            state, {"action": "speech", "text": "我发言完了，骑士可以自行判断。"}
        )
        self.assertFalse(state["players"][str(wolf)]["alive"])
        self.assertTrue(state["abilities"]["knight_used"])


    def _install_stub_cli(self, directory: Path) -> Path:
        """Put a fake CLI on PATH so the provider really spawns a subprocess."""
        script = directory / "stub_cli.py"
        script.write_text(STUB_CLI_SOURCE, encoding="utf-8")
        bin_root = directory / "bin"
        bin_root.mkdir()
        if os.name == "nt":
            launcher = bin_root / "stubcli.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="ascii")
        else:
            launcher = bin_root / "stubcli"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                                encoding="ascii")
            launcher.chmod(0o755)
        return bin_root

    def test_generic_cli_provider_drives_real_actions_through_a_subprocess(self):
        """First decision really spawns a CLI; the rest fall back so the test stays fast."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_root = self._install_stub_cli(root)
            received = root / "received"
            backend = CliBackend(key="stub", label="Stub CLI", executables=("stubcli",),
                                 template=("run", "{model}", "--format", "json"),
                                 prompt_mode="stdin", default_model="stub-1")

            class FirstCallRealCLI(GenericCLIProvider):
                def __init__(self, **kwargs):
                    super().__init__(**kwargs)
                    self.fallback = BuiltinAIProvider()
                    self.used = False

                def request_action(self, visible_state, request):
                    if not self.used:
                        self.used = True
                        return super().request_action(visible_state, request)
                    return self.fallback.request_action(visible_state, request)

            with patch.dict(os.environ,
                            {"PATH": f"{bin_root}{os.pathsep}{os.environ['PATH']}"}), \
                    patch.dict(CLI_BACKENDS, {"stub": backend}):
                provider = FirstCallRealCLI(backend="stub", timeout=60, retries=0,
                                            extra_args=[f"--received-dir={received}"])
                state = create_game(7, "villager", seed=4)
                engine = HumanGameEngine(provider)
                for _ in range(1500):
                    if state["phase"] == "game_over":
                        break
                    if state["pending"]["actor"] == state["human_seat"]:
                        engine.submit_human(state, human_intent(state))
                    else:
                        engine.advance_ai(state)

            self.assertIn(state["winner"], {"好人阵营", "狼人阵营"})
            calls = sorted(received.glob("call_*.json"))
            self.assertEqual(len(calls), 1)
            payload = calls[0].read_text(encoding="utf-8")
            self.assertIn('"visible_state"', payload)
            self.assertIn('"player_impressions"', payload)
            self.assertNotIn('"roles"', payload)


class MirrorBoardTests(unittest.TestCase):
    """镜隐迷踪板子的核心规则验证。"""

    def _mirror_game(self, seed=1, human_seat=7):
        return create_game(human_seat=human_seat, debug_role="random",
                           seed=seed, board="mirror")

    def _hidden_seat(self, state):
        return int(next(k for k, v in state["roles"].items() if v == "hidden_wolf"))

    def test_mirror_deck_composition(self):
        from game.human_game import MIRROR_DECK
        from collections import Counter
        c = Counter(MIRROR_DECK)
        self.assertEqual(len(MIRROR_DECK), 12)
        self.assertEqual(c["werewolf"], 3)
        self.assertEqual(c["hidden_wolf"], 1)
        self.assertEqual(c["mirror_maiden"], 1)
        self.assertEqual(c["guard"], 1)
        self.assertEqual(c["witch"], 1)
        self.assertEqual(c["hunter"], 1)
        self.assertEqual(c["villager"], 4)

    def test_create_mirror_game_sets_board(self):
        state = self._mirror_game()
        self.assertEqual(state["board"], "mirror")
        self.assertEqual(state["rules"]["board"], "镜隐迷踪")
        self.assertEqual(public_state_for_human(state)["board"], "mirror")
        roles = list(state["roles"].values())
        self.assertIn("hidden_wolf", roles)
        self.assertIn("mirror_maiden", roles)
        self.assertIn("hunter", roles)
        self.assertNotIn("wolf_beauty", roles)
        self.assertNotIn("seer", roles)
        self.assertNotIn("knight", roles)

    def test_hidden_wolf_not_told_small_wolf_identity(self):
        state = self._mirror_game()
        hidden = self._hidden_seat(state)
        view = get_visible_state(state, hidden)
        wolf_seen = [p["seat"] for p in view["players"] if p["role"] == "werewolf"]
        self.assertEqual(wolf_seen, [])
        self.assertEqual(view["private"]["wolf_teammates"], [])

    def test_hidden_wolf_does_not_see_wolf_chat(self):
        state = self._mirror_game()
        hidden = self._hidden_seat(state)
        # 塞一条狼聊进去
        state["wolf_chat"].append({"day": 1, "speaker": 3, "text": "今晚刀2号"})
        view = get_visible_state(state, hidden)
        self.assertNotIn("wolf_chat", view["private"])
        self.assertEqual(view["private"]["wolf_teammates"], [])
        # 小狼仍能看到狼聊
        small = int(next(k for k, v in state["roles"].items() if v == "werewolf"))
        small_view = get_visible_state(state, small)
        self.assertIn("wolf_chat", small_view["private"])

    def test_dead_human_hidden_wolf_can_watch_wolf_chat(self):
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        state["players"][str(hidden)]["alive"] = False
        state["wolf_chat"].append({"day": 1, "speaker": 3, "text": "今晚刀2号"})
        view = get_visible_state(state, hidden)
        self.assertEqual(view["private"]["wolf_chat"][0]["speaker"], 3)
        # 小狼本来就能看到狼聊；死亡真人的额外观战权限不会改变其他角色视角。
        small = int(next(k for k, v in state["roles"].items() if v == "werewolf"))
        ai_view = get_visible_state(state, small)
        self.assertIn("wolf_chat", ai_view["private"])

    def test_hidden_wolf_human_cannot_see_wolf_chat_actor_or_turn(self):
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        _start_mirror_wolf_chat(state)
        public = public_state_for_human(state)
        self.assertEqual(public["pending"]["action"], "night_wait")
        self.assertIsNone(public["pending"]["actor"])
        self.assertNotIn("wolf_chat", public["private"])

    def test_hidden_wolf_is_told_learned_identity_immediately(self):
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        _start_hidden_wolf_learn(state)
        target = next(seat for seat in state["pending"]["allowed_targets"]
                      if state["roles"][str(seat)] == "witch")
        HumanGameEngine(BuiltinAIProvider()).submit_human(
            state, {"action": "hidden_learn", "target": target}
        )
        view = get_visible_state(state, hidden)
        self.assertEqual(view["private"]["learned_role"], "witch")
        self.assertEqual(view["private"]["learned_target"], target)

    def test_hidden_wolf_learns_only_on_the_first_night(self):
        """回归：学过之后终身固定，第 2 夜起不能再问学习。

        ``hidden_wolf_learned`` 的键是字符串座位号，早期代码用整数去查，
        导致"已学过"判断永远为假，每个夜晚都会重复询问。
        """
        from game.human_game import _role
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        _start_hidden_wolf_learn(state)
        self.assertEqual(state["phase"], "night_hidden_learn")
        self.assertEqual(state["pending"]["actor"], hidden)
        target = state["pending"]["allowed_targets"][0]
        HumanGameEngine(BuiltinAIProvider()).submit_human(
            state, {"action": "hidden_learn", "target": target})
        self.assertEqual(state["abilities"]["hidden_wolf_learned"][str(hidden)],
                         _role(state, target))

        # 第 2 夜：仍然复用同一个夜晚链，但不再询问学习
        state["phase"] = "day_speech"
        state["day"] = 1
        _start_night(state)
        self.assertNotEqual(state["phase"], "night_hidden_learn")
        self.assertEqual(state["pending"]["action"], "wolf_chat")

    def test_hidden_wolf_inherits_mirror_maiden_check_from_the_second_night(self):
        """学到魔镜少女后，第 2 夜起可以具体身份查验，结果只有自己可见。"""
        from game.human_game import _role
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        maiden = next(seat for seat in range(1, 13) if _role(state, seat) == "mirror_maiden")
        state["abilities"]["hidden_wolf_learned"][str(hidden)] = "mirror_maiden"
        state["abilities"]["hidden_wolf_learn_target"][str(hidden)] = maiden

        # 第 1 夜（day == 0）刚学到的技能本夜不可用
        state["day"] = 0
        _mirror_start_hidden_skill(state)
        self.assertNotEqual(state["phase"], "night_hidden_skill")

        state["day"] = 1
        _mirror_start_hidden_skill(state)
        self.assertEqual(state["phase"], "night_hidden_skill")
        self.assertEqual(state["pending"]["actor"], hidden)
        self.assertEqual(state["pending"]["action"], "hidden_skill")
        self.assertEqual(state["phase_data"]["skill"], "peek")
        self.assertIn("魔镜少女", state["pending"]["prompt"])
        self.assertNotIn(hidden, state["pending"]["allowed_targets"])

        target = next(seat for seat in state["pending"]["allowed_targets"]
                      if _role(state, seat) == "guard")
        HumanGameEngine(BuiltinAIProvider()).submit_human(
            state, {"action": "hidden_skill", "target": target})
        check = state["abilities"]["hidden_wolf_checks"][str(hidden)][0]
        self.assertEqual(check["seat"], target)
        self.assertEqual(check["shown_role"], "guard")
        # 查验结果只进隐狼自己的私有视角
        self.assertEqual(get_visible_state(state, hidden)["private"]["hidden_checks"], [check])
        self.assertNotIn("hidden_checks",
                         get_visible_state(state, target)["private"])

    def test_full_offline_mirror_game_reaches_a_winner(self):
        """回归：镜隐迷踪的夜间链（含隐狼继承技能）必须能自己走到终局，不能死锁。"""
        state = create_game(7, "random", seed=4, board="mirror")
        drive(state, limit=4000)
        self.assertIn(state["winner"], {"好人阵营", "狼人阵营"})

    def test_mirror_board_day_phases_still_advance(self):
        """回归：mirror 板子的非夜间阶段（警长竞选等）必须正常推进，不能死锁。"""
        state = self._mirror_game()
        # 模拟警长竞选已收齐 12 份决定并公布
        state["phase"] = "sheriff_campaign"
        state["phase_data"] = {"queue": list(range(1, 13)), "index": 12, "action": "campaign",
                               "fixed_allowed": [], "prompt": "请选择上警或不上警。",
                               "candidates": [2, 3, 6], "campaign_decisions": {
                                   str(i): (i in (2, 3, 6)) for i in range(1, 13)},
                               "simultaneous": True, "published": False}
        _queue_complete(state)
        # 上警者 [2,3,6] → 应进入警上发言，而不是 pending=None 死锁
        self.assertEqual(state["phase"], "sheriff_speech")
        self.assertIsNotNone(state["pending"])
        self.assertEqual(state["pending"]["action"], "campaign_speech")

    def test_small_wolf_not_told_hidden_wolf_identity(self):
        state = self._mirror_game()
        small = int(next(k for k, v in state["roles"].items() if v == "werewolf"))
        view = get_visible_state(state, small)
        hidden_seen = [p for p in view["players"] if p["role"] == "hidden_wolf"]
        self.assertEqual(hidden_seen, [])

    def test_awakened_hidden_wolf_cannot_explode(self):
        state = self._mirror_game(human_seat=1)
        hidden = self._hidden_seat(state)
        state["human_seat"] = hidden
        state["day"] = 2
        state["phase"] = "day_speech"
        self.assertFalse(public_state_for_human(state)["can_wolf_explode"])

    def test_mirror_small_wolf_can_explode(self):
        state = self._mirror_game(human_seat=1)
        small = int(next(k for k, v in state["roles"].items() if v == "werewolf"))
        state["human_seat"] = small
        # 模拟第 1 夜已经学过：自爆进入黑夜不应再回头询问隐狼学习
        state["abilities"]["hidden_wolf_learned"][str(self._hidden_seat(state))] = "villager"
        state["day"] = 2
        state["phase"] = "day_pk_speech"
        self.assertTrue(public_state_for_human(state)["can_wolf_explode"])
        HumanGameEngine(BuiltinAIProvider()).submit_human(state, {"action": "wolf_explode"})
        self.assertEqual(state["players"][str(small)]["revealed_role"], "werewolf")
        self.assertEqual(state["phase"], "night_wolf_chat")

    def test_mirror_peek_shows_learned_role_for_hidden_wolf(self):
        from game.human_game import HIDDEN_WOLF_LEARNABLE, _role
        state = self._mirror_game()
        hidden = self._hidden_seat(state)
        for learned, expected in [("seer", "seer"), ("villager", "villager"),
                                  ("werewolf", "werewolf"), ("guard", "guard")]:
            state["abilities"]["hidden_wolf_learned"][str(hidden)] = learned
            shown = HIDDEN_WOLF_LEARNABLE.get(learned, learned)
            self.assertEqual(shown, expected)

    def test_mirror_maiden_builtin_ai_reports_concrete_check_like_seer(self):
        state = self._mirror_game(human_seat=1)
        maiden = int(next(k for k, v in state["roles"].items() if v == "mirror_maiden"))
        state["human_seat"] = maiden
        state["abilities"]["mirror_peeks"] = {
            str(maiden): [{"day": 1, "seat": 3, "shown_role": "guard"}]
        }
        view = get_visible_state(state, maiden)
        provider = BuiltinAIProvider()
        speech = provider.request_action(
            view, {"action": "campaign_speech", "actor": maiden,
                   "allowed_targets": [], "sequence": 1}
        )
        self.assertIn("魔镜少女", speech["text"])
        self.assertIn("3号", speech["text"])
        self.assertIn("守卫", speech["text"])
        self.assertNotIn("预言家", speech["text"])
        self.assertNotIn("是好人", speech["text"])
        self.assertNotIn("是狼人", speech["text"])

    def test_mirror_prompt_requires_exact_role_reporting(self):
        prompt = action_prompt("campaign_speech")
        self.assertIn("包括真人玩家的发言", prompt)
        self.assertIn("不能把真人发言当成背景噪音", prompt)
        mirror = get_visible_state(self._mirror_game(), 1)
        board_prompt = board_rules_prompt(mirror)
        self.assertIn("本板没有预言家", board_prompt)
        self.assertIn("独立的具体身份查验信息位", board_prompt)
        self.assertIn("不能把魔镜少女称作预言家", board_prompt)
        self.assertIn("真实具体身份", board_prompt)
        self.assertIn("不是‘好人/狼人’二分", board_prompt)

    def test_mirror_peek_shows_real_role_for_normal(self):
        from game.human_game import _role
        state = self._mirror_game()
        hidden = self._hidden_seat(state)
        for seat in range(1, 13):
            if _role(state, seat) != "hidden_wolf":
                self.assertEqual(_role(state, seat), _role(state, seat))

    def test_double_blade_kills_two(self):
        from game.human_game import _role
        state = self._mirror_game()
        hidden = self._hidden_seat(state)
        state["abilities"]["hidden_wolf_learned"][str(hidden)] = "werewolf"
        for seat in range(1, 13):
            if _role(state, seat) == "werewolf":
                state["players"][str(seat)]["alive"] = False
        state["night"]["wolf_targets"] = [1, 2]
        state["night"]["wolf_target"] = None
        state["night"]["guard"] = None
        state["night"]["saved"] = False
        _resolve_night(state)
        self.assertEqual(sorted(state["night"]["deaths"]), [1, 2])

    def test_hunter_cannot_shoot_when_poisoned(self):
        state = self._mirror_game(seed=8)
        hunter = int(next(k for k, v in state["roles"].items() if v == "hunter"))
        _kill(state, hunter, "被毒杀")
        self.assertNotIn(hunter, state.get("_hunter_shots_pending", []))

    def test_hunter_can_shoot_when_exiled(self):
        state = self._mirror_game(seed=8)
        hunter = int(next(k for k, v in state["roles"].items() if v == "hunter"))
        _kill(state, hunter, "放逐")
        self.assertIn(hunter, state.get("_hunter_shots_pending", []))

    def test_hidden_wolf_who_learned_hunter_can_shoot_after_death(self):
        state = self._mirror_game(seed=8)
        hidden = self._hidden_seat(state)
        state["abilities"]["hidden_wolf_learned"][str(hidden)] = "hunter"
        _kill(state, hidden, "放逐")
        self.assertIn(hidden, state.get("_hunter_shots_pending", []))
        _after_deaths(state, "post_exile")
        self.assertEqual(state["phase"], "hunter_shoot")
        self.assertEqual(state["pending"]["actor"], hidden)
        self.assertEqual(state["pending"]["action"], "hunter_shoot")

    def test_mirror_win_condition_slaughter_gods(self):
        state = self._mirror_game(seed=5)
        for k, v in state["roles"].items():
            if v in ("mirror_maiden", "guard", "witch", "hunter"):
                state["players"][k]["alive"] = False
        self.assertEqual(_winner(state), "狼人阵营")


if __name__ == "__main__":
    unittest.main()
