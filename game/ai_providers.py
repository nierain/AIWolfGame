"""Replaceable decision providers for the interactive human game.

Providers receive only the acting player's visible state and return an intent.
They never receive, or mutate, the authoritative game state.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class ActionProvider(ABC):
    """Interface implemented by every AI decision backend."""

    name = "base"

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
            wants = role == "seer" or (role in {"werewolf", "wolf_beauty"} and seat % 2 == 0)
            wants = wants or persona in {"激进", "高手"} and seat % 3 == 0
            return {"action": action, "join": wants}
        if action == "withdraw":
            role = visible_state["self"]["role"]
            withdraw = role not in {"seer", "werewolf"} and seat % 2 == 1
            return {"action": action, "withdraw": withdraw}
        if action in {"vote", "wolf_kill", "divine", "guard", "charm", "sheriff_recommend"}:
            target = self._pick_target(visible_state, targets, action, key)
            return {"action": action, "target": target}
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
            if role in {"werewolf", "wolf_beauty"}:
                return f"我上警想替好人多拿一点信息。现在没有必要盲信强势发言，我会重点听{focus}号的视角和后续警徽流。"
            return f"我上警不是硬跳身份，主要想把自己的视角聊清楚。{focus}号刚才的发言我还没完全听懂，后面我会根据警徽票再站边。"
        if role == "seer" and private.get("seer_checks"):
            check = private["seer_checks"][-1]
            result = "狼人" if check["is_wolf"] else "好人"
            return f"我昨晚验了{check['seat']}号，是{result}。我现在更关注{focus}号前后逻辑有没有变化，今天先围绕验人和票型来出。"
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
        wolf_side = role in {"werewolf", "wolf_beauty"}
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
            "witch": "女巫", "knight": "骑士", "guard": "守卫", "villager": "平民",
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
                "instructions": "只返回一个符合 request 约束的 JSON 行动，不要修改游戏状态。",
                "request": request,
                "visible_state": visible_state,
            }
            task_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return None


class CodexCLIProvider(ActionProvider):
    """Ask the locally authenticated Codex CLI for one isolated player action."""

    name = "codex_cli"

    def __init__(self, timeout: int = 180, model: Optional[str] = None):
        self.timeout = timeout
        self.model = model
        self.executable = shutil.which("codex.cmd") or shutil.which("codex")
        if not self.executable:
            raise ValueError("没有找到 Codex CLI，请先安装并登录 Codex。")

    def request_action(self, visible_state, request):
        action = request["action"]
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "const": action},
                "target": {"type": ["integer", "null"]},
                "text": {"type": ["string", "null"]},
                "join": {"type": ["boolean", "null"]},
                "withdraw": {"type": ["boolean", "null"]},
                "use": {"type": ["boolean", "null"]},
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
            "required": ["action", "target", "text", "join", "withdraw", "use", "direction", "player_impressions"],
            "additionalProperties": False,
        }
        payload = json.dumps(
            {"request": request, "visible_state": visible_state},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt = self._prompt(action)
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
                if not environment.get("CODEX_HOME"):
                    codex_home = Path.home() / ".codex"
                    if codex_home.is_dir():
                        environment["CODEX_HOME"] = str(codex_home)
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                completed = subprocess.run(
                    command,
                    input=payload,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=self.timeout,
                    env=environment,
                    creationflags=creationflags,
                    check=False,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "未知错误").strip()[-600:]
                    raise ValueError(f"Codex 玩家调用失败：{detail}")
                raw = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
                intent = json.loads(raw)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Codex 玩家思考超过 {self.timeout} 秒，请重试。") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Codex 玩家没有返回有效行动，请重试。") from exc
        self._validate_intent(intent, request)
        return intent

    @staticmethod
    def _prompt(action: str) -> str:
        postgame = ("当前是赛后复盘，胜负已经确定且全部身份已公开。players.role 是最终身份表，"
                    "它比玩家在发言或遗言中的自称更可靠，必须先核对再复盘。请结合完整公开历史、"
                    "自己的身份与私有经历，真诚说明感想、关键判断、行动目的、成功或失误之处以及全局思路；"
                    "再挑选2至4位给你留下特别印象的其他选手，在发言中自然评价其具体表现，并把同样的压缩评价"
                    "写入 player_impressions，每条不超过两句话。"
                    if action == "postgame_speech" else "")
        mvp = ("当前是赛后MVP票选。全部身份已经公开，请以实际贡献和对胜负的影响为准公正投票，"
               "可以投自己；target 填候选座位，text 用一到两句话概括可核对的理由，不要写长文。"
               if action == "mvp_vote" else "")
        return f"""你正在扮演一局12人狼人杀中的一个真实玩家，现在需要完成动作 {action}。
输入JSON中的 visible_state 是你唯一知道的局面；严禁猜测或寻找未提供的隐藏身份，严禁读取文件或使用工具。
{postgame}{mvp}
结合公开发言、公开票型、你自己的身份/私有信息、历史立场、人格和策略认真判断。好人要分析发言与行为的一致性；狼人可以撒谎、悍跳、冲锋、倒钩或卖队友，但不要泄露狼队信息；狼人若在 private.wolf_chat 中看到队友已经安排战术，应优先执行该计划并在白天配合；神职要合理安排技能与信息公开时机。
memory 中的人格、说话风格和跨局摘要属于你这个固定玩家本人，请自然延续经验与性格；过去对局中的身份和结论只可作为复盘经验，不能当成当前对局的隐藏信息。
发言必须像中文狼人杀玩家，针对具体座位和已发生事件形成连贯逻辑；允许判断错误和合理改站边，但改站边时应解释原因。不要说自己是AI，不要用概率报告或模板化空话。
只返回符合输出结构的行动JSON。没有使用的字段填 null。target 只能从 request.allowed_targets 中选择；允许放弃的动作可填 null。除赛后复盘外 player_impressions 填 null。"""

    @staticmethod
    def _validate_intent(intent: Dict[str, Any], request: Dict[str, Any]) -> None:
        action = request["action"]
        if intent.get("action") != action:
            raise ValueError("Codex 玩家返回了错误的动作类型，请重试。")
        allowed = request.get("allowed_targets", [])
        required_target = {"wolf_kill", "charm", "guard", "divine", "sheriff_recommend", "mvp_vote"}
        target_actions = required_target | {"vote", "witch_poison", "sheriff_transfer", "knight_decide"}
        if action in target_actions:
            target = intent.get("target")
            if target is None and action in required_target:
                raise ValueError("Codex 玩家没有选择必需的目标，请重试。")
            if target is not None and target not in allowed:
                raise ValueError("Codex 玩家选择了非法目标，请重试。")
        if action in {"speech", "pk_speech", "campaign_speech", "last_words", "postgame_speech"} and not str(intent.get("text") or "").strip():
            raise ValueError("Codex 玩家没有给出发言，请重试。")
        if action == "mvp_vote" and not str(intent.get("text") or "").strip():
            raise ValueError("Codex 玩家没有给出MVP票选理由，请重试。")
        if action == "postgame_speech":
            impressions = intent.get("player_impressions")
            if not isinstance(impressions, list) or not 2 <= len(impressions) <= 4:
                raise ValueError("赛后复盘需要评价2至4位特别选手")
        if action == "campaign" and not isinstance(intent.get("join"), bool):
            raise ValueError("Codex 玩家没有决定是否上警，请重试。")
        if action == "withdraw" and not isinstance(intent.get("withdraw"), bool):
            raise ValueError("Codex 玩家没有决定是否退水，请重试。")
        if action == "witch_save" and not isinstance(intent.get("use"), bool):
            raise ValueError("Codex 玩家没有决定是否使用解药，请重试。")
        if action == "sheriff_order" and intent.get("direction") not in {"forward", "reverse"}:
            raise ValueError("Codex 警长没有选择合法发言方向，请重试。")


def create_provider(name: str, bridge_root: Optional[Path] = None,
                    model: Optional[str] = None) -> ActionProvider:
    if name == "builtin":
        return BuiltinAIProvider()
    if name == "codex":
        if bridge_root is None:
            raise ValueError("Codex Provider 需要本地桥接目录")
        return CodexFileProvider(bridge_root)
    if name == "codex-cli":
        return CodexCLIProvider(model=model)
    raise ValueError(f"未知 AI Provider: {name}")
