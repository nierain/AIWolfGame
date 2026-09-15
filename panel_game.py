"""Local browser panel for a human-versus-bots Werewolf game."""

from __future__ import annotations

import argparse
import json
import threading
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
STATE_FILE = ROOT / ".panel_game_state.json"
LOCK = threading.Lock()

ROLES = {
    1: "knight", 2: "villager", 3: "werewolf", 4: "seer",
    5: "villager", 6: "wolf_beauty", 7: "guard", 8: "villager",
    9: "werewolf", 10: "witch", 11: "villager", 12: "werewolf",
}
ROLE_LABELS = {
    "knight": "骑士", "villager": "平民", "werewolf": "狼人",
    "seer": "预言家", "wolf_beauty": "狼美人", "guard": "守卫",
    "witch": "女巫",
}
WOLVES = {seat for seat, role in ROLES.items() if role in {"werewolf", "wolf_beauty"}}
GODS = {seat for seat, role in ROLES.items() if role in {"knight", "seer", "guard", "witch"}}
VILLAGERS = {seat for seat, role in ROLES.items() if role == "villager"}

DAY_ONE_SPEECHES = {
    5: "我警下把票投给4号。4号敢给后置位9号查杀，验人和警徽流都比较完整；9号的反查杀更像被迫起跳。我目前站4号，今天倾向出9号。同时会关注投9号的3号和12号，6号虽然上警不能投票，但他的站边也需要解释。",
    6: "我上警时就对4号查杀9号的力度持保留意见。5号说9号像被迫起跳，但真预言家接到查杀也只能起跳，不能只凭起跳时序定身份。1号认为4号搏杀真预的概率不低却仍站4号，5号又顺着票型点3号和12号，我觉得好人面不能仅由警徽票决定。我目前偏站9号，今天更想出4号，但会继续听双方后续发言。",
    7: "我警下把票投给4号。4号先手给9号查杀，9号反手给4号查杀，在我这里更像狼人最省力的悍跳格式。6号没有解释9号的验人心路，只是在替9号解套、攻击1号和5号，我觉得6号与9号可能共边。平安夜不能直接抬高任何人的身份，我今天站4号，倾向出9号。",
    8: "我警下也把票投给4号，目前还是更相信4号。4号先报9号查杀，9号再反查杀4号，确实是9号的悍跳面更大。不过7号直接盘6号和9号共边有些快，6号警上警后的态度至少是一致的。9号待会需要讲清楚为什么首夜验4号，以及他的警徽流10号、2号是怎么定的。今天我暂时倾向出9号，但会听完再落票。",
    9: "我底牌是预言家，昨夜验4号是狼人。4号敢往后置位发查杀，就是在赌后面没有真预言家；我接到查杀只能起跳。我的警徽流仍然是10号、2号。5号、7号、8号都用起跳时序压我，却没人解释4号为什么首夜精准搏杀到我。今天必须出4号，6号和12号是目前愿意独立思考的牌。",
    10: "我警下投给4号。9号说4号精准搏杀，但这不能反证9号是真预言家；9号的发言一直在解释自己为什么起跳，却很少分析4号以外的狼坑。6号对9号的保护比较明显，12号的票型也值得关注。我继续站4号，今天出9号。",
    11: "我投4号的原因很直接：4号先给查杀、警徽流也完整，9号的反查杀收益太高。8号提醒大家不要过早点6号和9号共边是对的，但6号确实需要给出更多独立视角。我今天先投9号，等翻牌和夜间信息再修正。",
    12: "我警下投9号。4号先手搏杀后置位的收益很高，9号接查杀后起跳并不自动等于狼人。现在多数人只是重复起跳顺序，没有验证4号的验人心路。我站9号，今天想投4号；如果9号出局，我会重点看5号和7号。",
    2: "听完一圈，我认为4号的预言家面仍然更高。9号把支持自己的人定义成独立思考，却没有正面回应自己的狼坑与警徽流。1号警上虽然表达得犹豫，但至少给出了搏杀概率这个思考过程。我今天倾向投9号，6号和12号留到明天继续听。",
    3: "我站9号。4号拿到警徽以后，5号、7号、10号、11号的发言几乎都在复述同一套时序逻辑，这种整齐未必是好事。9号接查杀起跳没有选择，不能因此被定狼。今天我会投4号，警徽票不能代替身份判断。",
    4: "我底牌预言家，昨夜查验9号是狼人。9号反手查杀我，是狼人在被查杀后最容易组织的悍跳。6号、12号明确站9号，3号警下给9号投票后也继续冲我，这三张牌都进入我的狼坑。今天统一出9号；我的验人顺序先看6号，再看12号。",
}


def add_event(state: Dict[str, Any], kind: str, text: str, speaker: Optional[int] = None,
              phase: Optional[str] = None) -> None:
    state["event_seq"] += 1
    state["history"].append({
        "id": state["event_seq"], "day": state["day"],
        "phase": phase or state["phase"], "kind": kind,
        "speaker": speaker, "text": text,
    })


def initial_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "schema": 1, "day": 1, "phase": "speech", "event_seq": 0,
        "sheriff": 4, "sheriff_flow": [6, 12], "direction": "顺序",
        "alive": list(range(1, 13)), "speech_order": [5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4],
        "speech_index": 3, "knight_used": False, "winner": None,
        "guard_last": 4, "witch_medicine": True, "witch_poison": True,
        "wolf_beauty_charm": 10, "seer_checks": [{"seat": 9, "is_wolf": True}],
        "last_exiled": None, "history": [],
    }
    add_event(state, "system", "第1夜为平安夜，12名玩家全部存活。", phase="night")
    add_event(state, "system", "1、2、4、6、9号上警；所有上警玩家均无警长投票权。", phase="sheriff")
    campaigns = {
        1: "按概率来说，4号后置位只有三个人发言，搏杀真预概率不低，还有可能后置位有狼队友，但我暂时站4号。",
        2: "我希望按验人逻辑和发言判断，目前不跳身份。",
        4: "我是真预言家，昨夜查验9号是狼人，警徽流6号、12号。",
        6: "我不跳预言家，对4号的查杀持保留意见，想先听9号。",
        9: "我是真预言家，昨夜查验4号是狼人，警徽流10号、2号。",
    }
    for seat in [1, 2, 4, 6, 9]:
        add_event(state, "speech", campaigns[seat], seat, "sheriff")
    add_event(state, "vote", "警下5、7、8、10、11号投4号，3、12号投9号；4号以5比2当选警长。", phase="sheriff")
    add_event(state, "system", "4号警长选择从下家5号开始顺序发言：5→6→7→8→9→10→11→12→1→2→3，4号最后归票。")
    for seat in [5, 6, 7, 8]:
        add_event(state, "speech", DAY_ONE_SPEECHES[seat], seat)
    return state


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return initial_state()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("schema") == 1:
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return initial_state()


def save_state(state: Dict[str, Any]) -> None:
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def current_speaker(state: Dict[str, Any]) -> Optional[int]:
    if state["phase"] != "speech":
        return None
    index = state["speech_index"]
    return state["speech_order"][index] if index < len(state["speech_order"]) else None


def bot_speech(state: Dict[str, Any], seat: int) -> str:
    if state["day"] == 1:
        return DAY_ONE_SPEECHES[seat]
    role = ROLES[seat]
    last_out = state.get("last_exiled")
    day_target = next((x for x in [6, 12, 3] if x in state["alive"]), None)
    if role == "seer":
        latest = state["seer_checks"][-1]
        result = "狼人" if latest["is_wolf"] else "好人"
        return f"我继续以预言家身份汇报：昨夜查验{latest['seat']}号是{result}。上一轮{last_out or '无人'}出局后，今天请围绕验人结果和冲票关系发言；我目前归票{day_target or '待定'}号。"
    if seat in WOLVES:
        alternatives = [x for x in [4, 1, 10, 5, 8, 11, 2] if x in state["alive"] and x != seat]
        target = alternatives[0] if alternatives else "待定"
        return f"上一轮{last_out or '无人'}出局后的票型需要重看。我不接受大家直接按旧狼坑连续出人，{target}号的身份和发言收益更值得怀疑。今天我会重点听{target}号，暂时不跟随警长的归票。"
    return f"上一轮{last_out or '无人'}出局给了我们新的票型信息。我会优先检查当时保护狼人牌、冲击预言家的人。当前我倾向出{day_target or '待定'}号，但希望后置位给出自己的理由，不要只复述结论。"


def append_bot_speech(state: Dict[str, Any]) -> None:
    speaker = current_speaker(state)
    if speaker is not None and speaker != 1:
        add_event(state, "speech", bot_speech(state, speaker), speaker)


def transfer_badge_if_needed(state: Dict[str, Any], dead: int) -> None:
    if state.get("sheriff") != dead:
        return
    target = next((seat for seat in state["sheriff_flow"] if seat in state["alive"]), None)
    state["sheriff"] = target
    if target:
        add_event(state, "system", f"{dead}号警长出局，警徽移交给{target}号。")
    else:
        add_event(state, "system", f"{dead}号警长出局，警徽被撕毁。")


def eliminate(state: Dict[str, Any], seat: int, cause: str, reveal: Optional[str] = None) -> None:
    if seat not in state["alive"]:
        return
    state["alive"].remove(seat)
    suffix = f"，身份为{reveal}" if reveal else ""
    add_event(state, "death", f"{seat}号因{cause}出局{suffix}。")
    transfer_badge_if_needed(state, seat)
    if seat == 6:
        charmed = state.get("wolf_beauty_charm")
        if charmed in state["alive"]:
            state["alive"].remove(charmed)
            add_event(state, "death", f"{charmed}号因狼美人殉情出局。")
            transfer_badge_if_needed(state, charmed)


def check_winner(state: Dict[str, Any]) -> Optional[str]:
    alive = set(state["alive"])
    if not (alive & WOLVES):
        return "好人阵营"
    if not (alive & GODS) or not (alive & VILLAGERS):
        return "狼人阵营"
    return None


def finish_if_needed(state: Dict[str, Any]) -> bool:
    winner = check_winner(state)
    if not winner:
        return False
    state["winner"] = winner
    state["phase"] = "game_over"
    add_event(state, "system", f"游戏结束：{winner}胜利。全部身份已公开。")
    return True


def advance_speech(state: Dict[str, Any]) -> None:
    state["speech_index"] += 1
    if state["speech_index"] >= len(state["speech_order"]):
        state["phase"] = "vote"
        add_event(state, "system", "本轮发言结束，进入放逐投票。")
        return
    append_bot_speech(state)


def bot_vote_target(state: Dict[str, Any], voter: int) -> int:
    alive = state["alive"]
    if voter in WOLVES:
        choices = [state.get("sheriff"), 1, 10, 5, 8, 11, 2, 7, 4]
        return next(x for x in choices if isinstance(x, int) and x in alive and x not in WOLVES)
    priorities = [9, 6, 12, 3]
    return next((x for x in priorities if x in alive), next(x for x in alive if x != voter))


def resolve_vote(state: Dict[str, Any], user_target: Optional[int]) -> None:
    scores: Dict[int, float] = {}
    records = []
    for voter in list(state["alive"]):
        if voter == 1:
            if user_target is None:
                continue
            target = user_target
        else:
            target = bot_vote_target(state, voter)
        weight = 1.5 if voter == state.get("sheriff") else 1.0
        scores[target] = scores.get(target, 0.0) + weight
        records.append(f"{voter}→{target}{'（警长1.5票）' if weight == 1.5 else ''}")
    add_event(state, "vote", "；".join(records))
    highest = max(scores.values())
    tied = sorted(seat for seat, score in scores.items() if score == highest)
    if len(tied) > 1:
        add_event(state, "system", f"{','.join(map(str, tied))}号平票，复投后由多数票集中出局{tied[0]}号。")
    out = tied[0]
    state["last_exiled"] = out
    eliminate(state, out, "放逐投票")
    if finish_if_needed(state):
        return
    state["phase"] = "night"
    add_event(state, "system", f"第{state['day']}天结束，天黑请闭眼。")


def begin_day(state: Dict[str, Any]) -> None:
    sheriff = state.get("sheriff")
    alive = state["alive"]
    if sheriff in alive:
        circle = list(range(sheriff + 1, 13)) + list(range(1, sheriff + 1))
    else:
        circle = list(range(1, 13))
    state["speech_order"] = [seat for seat in circle if seat in alive]
    state["speech_index"] = 0
    state["phase"] = "speech"
    if sheriff in alive:
        add_event(state, "system", f"{sheriff}号警长选择从下家开始顺序发言：" + "→".join(map(str, state["speech_order"])))
    append_bot_speech(state)


def resolve_night(state: Dict[str, Any]) -> None:
    next_night = state["day"] + 1
    alive = state["alive"]
    victim = next((x for x in [4, 10, 7, 1, 5, 8, 11, 2] if x in alive and x not in WOLVES), None)
    guard = 7 in alive
    guard_choices = {2: 10, 3: 1, 4: 5}
    guarded = guard_choices.get(next_night)
    if guarded not in alive or guarded == state.get("guard_last"):
        guarded = next((x for x in alive if x != state.get("guard_last") and x not in WOLVES), None)
    if guard:
        state["guard_last"] = guarded

    saved = False
    if victim and state["witch_medicine"] and 10 in alive and victim in {4, 1, 7}:
        state["witch_medicine"] = False
        saved = True

    poisoned = None
    if not saved and state["witch_poison"] and 10 in alive and next_night >= 3:
        poisoned = next((x for x in [12, 3, 6] if x in alive), None)
        if poisoned:
            state["witch_poison"] = False

    if 6 in alive:
        state["wolf_beauty_charm"] = next((x for x in [7, 10, 1, 5, 8] if x in alive and x not in WOLVES), None)
    if 4 in alive:
        checked_before = {x["seat"] for x in state["seer_checks"]}
        target = next((x for x in [6, 12, 3, 5, 8] if x in alive and x not in checked_before and x != 4), None)
        if target:
            state["seer_checks"].append({"seat": target, "is_wolf": target in WOLVES})

    deaths = []
    if victim and victim != guarded and not saved:
        deaths.append(victim)
    if poisoned and poisoned not in deaths:
        deaths.append(poisoned)
    for seat in deaths:
        if seat in state["alive"]:
            state["alive"].remove(seat)
            transfer_badge_if_needed(state, seat)
    if deaths:
        add_event(state, "death", "天亮了，昨夜死亡玩家：" + "、".join(f"{x}号" for x in deaths) + "。")
    else:
        add_event(state, "system", "天亮了，昨夜为平安夜。")
    if finish_if_needed(state):
        return
    state["day"] += 1
    begin_day(state)


def apply_action(state: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action")
    speaker = current_speaker(state)
    if action == "advance":
        if state["phase"] != "speech" or speaker == 1:
            raise ValueError("当前不能推进AI发言")
        advance_speech(state)
    elif action == "speak":
        if state["phase"] != "speech" or speaker != 1 or 1 not in state["alive"]:
            raise ValueError("现在没有轮到1号发言")
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("发言不能为空")
        add_event(state, "speech", text[:2000], 1)
        advance_speech(state)
    elif action == "duel":
        if state["phase"] != "speech" or state["knight_used"] or 1 not in state["alive"]:
            raise ValueError("当前不能发动骑士决斗")
        target = int(payload.get("target", 0))
        if target not in state["alive"] or target == 1:
            raise ValueError("决斗目标无效")
        state["knight_used"] = True
        add_event(state, "ability", f"1号骑士翻牌，向{target}号发起决斗！", 1)
        if target in WOLVES:
            eliminate(state, target, "骑士决斗", ROLE_LABELS[ROLES[target]])
            if not finish_if_needed(state):
                state["phase"] = "night"
                add_event(state, "system", "骑士决斗命中狼人，本日立即结束并进入黑夜。")
        else:
            eliminate(state, 1, "骑士决斗失败", "骑士")
            add_event(state, "system", f"{target}号为好人，白天流程继续。")
            if speaker == 1 and state["phase"] == "speech":
                advance_speech(state)
            finish_if_needed(state)
    elif action == "vote":
        if state["phase"] != "vote":
            raise ValueError("当前不是投票阶段")
        target = payload.get("target")
        user_target = int(target) if target not in (None, "", "abstain") else None
        if 1 in state["alive"] and user_target is not None and user_target not in state["alive"]:
            raise ValueError("投票目标无效")
        resolve_vote(state, user_target)
    elif action == "night":
        if state["phase"] != "night":
            raise ValueError("当前不是夜晚阶段")
        resolve_night(state)
    else:
        raise ValueError("未知操作")
    return state


def public_state(state: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(state)
    result.pop("seer_checks", None)
    result.pop("wolf_beauty_charm", None)
    result.pop("guard_last", None)
    result.pop("witch_medicine", None)
    result.pop("witch_poison", None)
    result["current_speaker"] = current_speaker(state)
    result["players"] = [
        {"seat": seat, "alive": seat in state["alive"], "sheriff": seat == state.get("sheriff"),
         "user": seat == 1, "role": ROLE_LABELS[ROLES[seat]] if state["phase"] == "game_over" or seat == 1 else None}
        for seat in range(1, 13)
    ]
    result["private"] = {"seat": 1, "role": "骑士", "duel_available": not state["knight_used"] and 1 in state["alive"]}
    return result


class PanelHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        relative = urlparse(path).path.lstrip("/") or "panel_game.html"
        return str(WEB_ROOT / relative)

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
        if urlparse(self.path).path == "/api/state":
            with LOCK:
                self.send_json(public_state(load_state()))
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
                state = load_state()
                apply_action(state, payload)
                save_state(state)
                response = public_state(state)
            self.send_json(response)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[panel] {self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="启动狼人杀本地交互面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reset", action="store_true", help="重置为当前对局的第1天8号发言")
    args = parser.parse_args()
    if args.reset or not STATE_FILE.exists():
        save_state(initial_state())
    server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    print(f"狼人杀面板已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
