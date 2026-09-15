"""
AI 玩家系统

主要功能：
1. 统一的 AI 代理接口
2. 区分狼人、神职和村民阵营的代理
3. 维护对话历史和游戏状态记忆
4. 统一使用 OpenAI API 进行调用
"""

from typing import Optional, Dict, Any, List
from openai import OpenAI
import logging
import re
from .roles import BaseRole, RoleType
import random

class Memory:
    def __init__(self):
        self.conversations: List[Dict] = []  # 所有对话记录
        self.game_results: List[Dict] = []   # 每轮游戏结果
        self.current_round_discussions: List[Dict] = []  # 当前回合的讨论记录

    def add_conversation(self, conversation: Dict):
        """添加对话记录
        
        Args:
            conversation: 包含回合、阶段、说话者和内容的字典
        """
        self.conversations.append(conversation)
        if conversation.get("phase") == "discussion":
            self.current_round_discussions.append(conversation)

    def add_game_result(self, result: Dict):
        self.game_results.append(result)

    def get_current_round_discussions(self) -> List[Dict]:
        """获取当前回合的所有讨论"""
        return self.current_round_discussions

    def clear_current_round(self):
        """清空当前回合的讨论记录"""
        self.current_round_discussions = []

    def get_recent_conversations(self, count: int = 5) -> List[Dict]:
        """获取最近的几条对话记录，并格式化为易读的形式"""
        recent = self.conversations[-count:] if self.conversations else []
        formatted = []
        for conv in recent:
            if conv.get("phase") == "discussion":
                formatted.append(f"{conv.get('speaker', '未知')}说：{conv.get('content', '')}")
        return formatted

    def get_all_conversations(self) -> str:
        """获取所有对话记录的格式化字符串"""
        if not self.conversations:
            return "暂无历史记录"
            
        formatted = []
        current_round = None
        
        for conv in self.conversations:
            # 如果是新的回合，添加回合标记
            if current_round != conv.get("round"):
                current_round = conv.get("round")
                formatted.append(f"\n=== 第 {current_round} 回合 ===\n")
            
            if conv.get("phase") == "discussion":
                formatted.append(f"{conv.get('speaker', '未知')}说：{conv.get('content', '')}")
            elif conv.get("phase") == "vote":
                formatted.append(f"{conv.get('speaker', '未知')}投票给了{conv.get('target', '未知')}，理由：{conv.get('content', '')}")
            elif conv.get("phase") == "death":
                formatted.append(f"{conv.get('speaker', '未知')}的遗言：{conv.get('content', '')}")
        
        return "\n".join(formatted)

class BaseAIAgent:
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        self.config = config
        self.role = role
        self.logger = logging.getLogger(__name__)
        self.memory = Memory()
        self.client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("baseurl")
        )

    def ask_ai(self, prompt, system_prompt=None, game_state=None, stream=True, speaker_name=None):
        """
        统一的 AI 调用接口，默认开启流式输出（逐字打印到终端）
        - speaker_name: 若提供，则在思考/发言前显示角色标签
        - 通过 config['show_reasoning'] 控制是否显示思考过程
        """
        GRAY = '\033[90m'
        RESET = '\033[0m'
        show_reasoning = self.config["show_reasoning"]

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            if stream:
                stream_response = self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=messages,
                    stream=True,
                    extra_body=self.config["extra_body"]
                )
                collected_content = []
                state = 'start'  # 'start', 'reasoning', 'content'

                for chunk in stream_response:
                    if not chunk.choices or not chunk.choices[0].delta:
                        continue

                    delta = chunk.choices[0].delta
                    reasoning = getattr(delta, 'reasoning_content', None)
                    content = delta.content

                    # ----- 推理内容（仅在需要显示时处理） -----
                    if reasoning and show_reasoning:
                        if state == 'start':
                            if speaker_name:
                                print(f"💭 {speaker_name} 的想法：\n{GRAY}", end='', flush=True)
                            else:
                                print(f"💭 {GRAY}", end='', flush=True)
                            state = 'reasoning'
                        elif state == 'content':
                            # 从发言切换回推理：重置颜色 + 换行
                            print(f"{RESET}\n\n💭 {GRAY}", end='', flush=True)
                            state = 'reasoning'
                        print(reasoning, end='', flush=True)

                    # 当不显示推理时，完全忽略 reasoning，也不改变 state
                    # （无需额外分支，静默跳过即可）

                    # ----- 正常内容 -----
                    if content:
                        if state == 'start':
                            if speaker_name:
                                print(f"💬 {speaker_name} 的发言：\n", end='', flush=True)
                            else:
                                print("💬 ", end='', flush=True)
                            state = 'content'
                        elif state == 'reasoning':
                            # 从推理切换到发言：重置颜色 + 换行 + 角色标签（如果有）
                            if speaker_name:
                                print(f"{RESET}\n\n💬 {speaker_name} 的发言：\n", end='', flush=True)
                            else:
                                print(f"{RESET}\n\n💬 ", end='', flush=True)
                            state = 'content'
                        # 输出本次内容
                        print(content, end='', flush=True)
                        collected_content.append(content)

                print(RESET)  # 确保最后颜色复原
                return ''.join(collected_content)

            else:
                # 非流式备用
                response = self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=messages,
                    extra_body=self.config.get("extra_body", None)
                )
                return response.choices[0].message.content

        except Exception as e:
            logging.error(f"AI 调用失败: {e}")
            import random
            excuses = [
                "刚才网络有点卡，没看清前面的讨论",
                "刚才走神了，能再说一下情况吗",
                "我这边刚才断了一下，现在才连上",
                "刚才在处理别的事情，没注意听",
                "我这边信号不好，刚才没听清"
            ]
            excuse = random.choice(excuses)
            fallback_text = f"【皱眉思考】{excuse}。这一轮我选择弃票，需要更多信息才能做出判断。弃票"
            print(fallback_text)
            return fallback_text
    
    def _extract_target(self, response: str) -> Optional[str]:
        try:
            # 1. 明确的投票
            vote_patterns = [
                r'选择\[([^\]]+)\]',             # 匹配 选择[player1] 
                r'选择[：:]\s*(\w+\d*)',          # 匹配 选择：player1
                r'选择\s*(\w+\d*)',              # 匹配 选择 player1
                r'我[的]?选择[是为]?\s*[：:"]?\s*(\w+\d*)',  # 匹配 我选择是player1
                r'投票(给|选择|选)\s*[：:"]?\s*(\w+\d*)',   # 匹配 投票给player1
            ]
            for pattern in vote_patterns:
                match = re.search(pattern, response)
                if match:
                    target = match.group(match.lastindex or 1).strip()
                    if re.match(r'^player\d+$', target):
                        return target

            # 2. 弃票
            if re.search(r'弃票|放弃投票|不投票|暂不投票|跳过投票', response):
                return None

            # 3. 模糊不定的投票兜底
            guess_patterns = [
                r'[我认为]*(\w+\d+)[最非常]*(可疑|是狼人|有问题)',  # 匹配 player1最可疑
                r'[决定|准备]*(投|投票|票)[给向](\w+\d+)',  # 匹配 投给player1
                r'\((\w+\d*)\)',                 # 匹配 (player1)
                r'([a-zA-Z]+\d+)\s*\(',          # 匹配 player1(
                r'.*\b(player\d+)\b.*',          # 最宽松匹配，尝试找到任何player+数字
            ]
            for pattern in guess_patterns:
                match = re.search(pattern, response)
                if match:
                    groups = match.groups()
                    target = next((g for g in reversed(groups) if g and re.match(r'^player\d+$', g)), None)
                    if target:
                        return target

            self.logger.warning(f"无法从响应中提取有效的目标ID: {response}")
            return None

        except Exception as e:
            self.logger.error(f"提取目标ID时出错: {str(e)}")
            return None
        
    def discuss(self, game_state: Dict[str, Any], speaker_name: Optional[str] = None) -> Dict[str, Any]:
        """讨论阶段"""
        prompt = self._generate_discussion_prompt(game_state)
        response = self.ask_ai(prompt, self._get_discussion_prompt(), game_state, speaker_name=speaker_name)
        
        # 记录讨论，包含说话者信息
        self.memory.add_conversation({
            "round": game_state["current_round"],
            "phase": "discussion",
            "speaker": self.role.name,
            "content": response
        })
        
        # 更新游戏状态中的讨论记录
        if "discussions" not in game_state:
            game_state["discussions"] = []
        game_state["discussions"].append({
            "speaker": self.role.name,
            "content": response
        })
        
        return {
            "type": "discuss",
            "content": response
        }

    def vote(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """投票决定
        
        Args:
            game_state: 游戏状态
            
        Returns:
            Dict 包含:
                - target: 投票目标ID
                - reason: 投票理由
        """
        prompt = self._generate_vote_prompt(game_state)
        response = self.ask_ai(prompt, self._get_vote_prompt(), game_state)
        
        # 从响应中提取目标ID和理由
        target = self._extract_target(response)
        
        # 获取当前玩家ID（使用player_id而不是name）
        current_player_id = None
        for pid, info in game_state['players'].items():
            if info['name'] == self.role.name:
                current_player_id = pid
                break
        
        # 添加防止自投的逻辑
        if target == current_player_id:
            self.logger.warning(f"{self.role.name} 试图投票给自己，重新选择一个随机目标")
            alive_players = [pid for pid, info in game_state['players'].items() 
                            if info['is_alive'] and pid != current_player_id]
            if alive_players:
                target = random.choice(alive_players)
        
        return {
            "target": target,
            "reason": response
        }

    def _generate_action_prompt(self) -> str:
        """生成动作和神色的提示词"""
        return """
        请在发言时加入动作和表情描写，要求：
        1. 用【】包裹动作和表情
        2. 描写要生动形象，符合角色身份
        3. 至少20个字的动作描写
        4. 动作要自然地融入发言中
        5. 表现出说话时的情绪变化
        """

    def _format_discussions(self, discussions: List[Dict]) -> str:
        """格式化讨论记录"""
        if not discussions:
            return "暂无讨论记录"
        
        formatted = []
        for disc in discussions:
            formatted.append(f"{disc['speaker']} 说：{disc['content']}")
        return "\n".join(formatted)

    def _get_discussion_prompt(self) -> str:
        """获取讨论的系统提示词"""
        return """你正在参与一场游戏讨论。请根据当前的游戏状态和讨论记录，给出合理的分析和判断。"""

    def _get_vote_prompt(self) -> str:
        """获取投票的系统提示词"""
        return """你正在根据讨论情况进行投票。
        要考虑：
        1. 分析局势，给出合理的判断
        2. 基于之前的讨论和发言做出选择
        3. 给出明确的投票决定和理由
        4. 用"选择[玩家ID]"格式说明投票决定
        5. 如果无法确定，可以说"弃票"
        """

    def last_words(self, game_state: Dict[str, Any]) -> str:
        """处理玩家的遗言"""
        prompt = f"""
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 你的身份: {self.role.role_type.value} {self.role.name}
        - 你即将死亡，这是你最后的遗言。
        
        请说出你的遗言：
        1. 可以揭示自己的真实身份
        2. 可以给出对局势的分析
        3. 可以给存活的玩家一些建议
        4. 发言要符合角色身份
        5. 加入适当的动作描写
        """
        
        response = self.ask_ai(prompt, self._get_last_words_prompt(), game_state)
        return response

    def _get_last_words_prompt(self) -> str:
        """获取遗言的系统提示词"""
        return """你正在发表临终遗言。
        要求：
        1. 符合角色身份特征
        2. 表达真挚的情感
        3. 可以给出重要的信息
        4. 为存活的玩家指明方向
        """

    def _get_role_name_cn(self) -> str:
        """获取角色的中文名称"""
        role_names = {
            "werewolf": "狼人",
            "villager": "村民",
            "seer": "预言家",
            "witch": "女巫",
            "hunter": "猎人"
        }
        return role_names.get(self.role.role_type.value, self.role.role_type.value)
    
    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        """生成讨论提示词，包含所有历史发言"""
        role_name_cn = self._get_role_name_cn()
        
        # 判断是否是夜间阶段
        is_night = game_state.get("phase") == "night"
        
        # 如果是狼人，添加队友信息（仅在夜间显示）
        teammate_info = ""
        if self.role.is_wolf() and hasattr(self, 'team_members') and is_night:
            teammate_names = [game_state['players'].get(tid, {}).get('name', tid) for tid in self.team_members]
            if teammate_names:
                teammate_info = f"\n- 你的狼队友: {', '.join(teammate_names)}"
        
        # 白天时狼人要隐藏身份，提示词中显示为"村民"
        display_role = "村民" if (self.role.is_wolf() and not is_night) else role_name_cn
        
        base_prompt = f"""
{self._generate_action_prompt()}

【重要】你的身份是: {display_role} (名字: {self.role.name})
{teammate_info}

当前游戏状态:
- 回合: {game_state['current_round']}
- 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}

当前回合的讨论记录：
{self._format_discussions(game_state.get('discussions', []))}

历史记录：
{self.memory.get_all_conversations()}

请根据以上信息，作为{display_role}，发表你的看法：
1. 分析其他玩家的行为和发言，找出可疑之处
2. 给出你的推理过程和判断依据
3. 表达对局势的看法
4. 发言要超过100字
5. 记得加入动作描写【】

注意：你必须始终牢记自己是{display_role}，根据{display_role}的立场来发言！
"""
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        role_name_cn = self._get_role_name_cn()
        
        # 判断是否是夜间阶段
        is_night = game_state.get("phase") == "night"
        
        # 检查是否是重新投票（平票后）
        revote_info = ""
        vote_restriction = ""
        if game_state.get("vote_context", {}).get("type") == "vote_context":
            vote_context = game_state["vote_context"]
            if vote_context.get("is_revote", False):
                tied_players = vote_context.get("tied_players", [])
                if tied_players:
                    tied_names = [game_state['players'].get(pid, {}).get('name', pid) for pid in tied_players]
                    vote_restriction = f"\n【重要】这是平票后的重新投票，你只能投给以下平票玩家：{', '.join(tied_names)}\n"
        
        # 如果是狼人，添加队友信息（仅在夜间显示）
        teammate_info = ""
        if self.role.is_wolf() and hasattr(self, 'team_members') and is_night:
            teammate_names = [game_state['players'].get(tid, {}).get('name', tid) for tid in self.team_members]
            if teammate_names:
                teammate_info = f"\n- 你的狼队友: {', '.join(teammate_names)} (不要投票给队友)"
        
        # 白天时狼人要隐藏身份，提示词中显示为"村民"
        display_role = "村民" if (self.role.is_wolf() and not is_night) else role_name_cn
        
        return f"""
【重要】你的身份是: {display_role} (名字: {self.role.name})
{teammate_info}
{vote_restriction}

当前游戏状态:
- 回合: {game_state['current_round']}
- 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() 
            if info['is_alive'] and pid != self.role.name]}

完整对话记录：
{self.memory.get_all_conversations()}

请详细说明你要投票给谁，以及投票的理由。注意：不能投票给自己！

要求：
1. 分析局势，给出合理的投票理由
2. 考虑其他玩家的发言和行为
3. 使用"我选择[player数字]"或"选择[player数字]"这样的格式来明确指出你的投票目标
4. player ID必须是完整的格式，如player1、player2等
5. 不能选择自己作为投票目标
6. 给出充分的理由，至少50字
7. 如果你认为信息不足，可以选择弃票，使用"弃票"或"放弃投票"来表示

重要提醒：你是{display_role}，请根据{display_role}的立场投票！

例如良好的投票格式：
"经过分析，我认为player3的行为最为可疑，他在第二轮的发言中自相矛盾，而且...（分析原因）...因此我选择[player3]"

弃票格式示例：
"目前信息不足，我需要更多时间观察，这一轮我选择弃票。"
"""

class WerewolfAgent(BaseAIAgent):
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.team_members: List[str] = []  # 狼队友列表

    def discuss(self, game_state: Dict[str, Any], speaker_name: Optional[str] = None) -> Dict[str, Any]:
        """狼人讨论"""
        prompt = self._generate_discussion_prompt(game_state)
        response = self.ask_ai(prompt, self._get_werewolf_discussion_prompt(), game_state, speaker_name=speaker_name)
            
        # 记录讨论
        self.memory.add_conversation({
            "round": game_state["current_round"],
            "phase": "discussion",
            "content": response
        })
        
        # 尝试解析JSON响应
        try:
            if game_state["phase"] == "night":
                # 夜间杀人讨论
                return {
                    "type": "kill",
                    "content": response,
                    "target": self._extract_target(response)
                }
            else:
                # 白天正常发言
                return {
                    "type": "discuss",
                    "content": response
                }
        except Exception as e:
            logging.error(f"解析响应失败: {str(e)}")
            return {
                "type": "error",
                "content": response,
                "target": "villager1"
            }

    def vote(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """根据讨论做出投票决定"""
        prompt = self._generate_vote_prompt(game_state)
        response = self.ask_ai(prompt, self._get_werewolf_vote_prompt(), game_state)
        
        # 从响应中提取目标ID和理由
        target = self._extract_target(response)
        
        # 获取当前玩家ID
        current_player_id = None
        for pid, info in game_state['players'].items():
            if info['name'] == self.role.name:
                current_player_id = pid
                break
        
        # 防止自投
        if target == current_player_id:
            self.logger.warning(f"{self.role.name} 试图投票给自己，重新选择")
            alive_players = [pid for pid, info in game_state['players'].items() 
                            if info['is_alive'] and pid != current_player_id]
            if alive_players:
                target = random.choice(alive_players)
        
        return {
            "target": target,
            "reason": response
        }

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        """重写狼人的讨论提示词，加入队友信息"""
        base_prompt = f"""
        {self._generate_action_prompt()}
        
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}
        - 你的身份: 狼人 {self.role.name}
        - 你的队友: {[game_state['players'][pid]['name'] for pid in self.team_members]}
        - 历史记录: {self.memory.get_recent_conversations()}
        """
        
        if game_state["phase"] == "night":
            return base_prompt + """
            作为狼人，请讨论今晚要杀死谁：
            1. 分析每个玩家的威胁程度，但不要说出具体角色
            2. 考虑每个人的行为特征
            3. 给出详细的理由
            4. 发言必须超过20个字
            5. 最后用"选择[玩家ID]"格式说明你的决定
            6. 不要在发言中透露你已经知道某个玩家的具体身份
            """
        else:
            return base_prompt + """
            请以好人的身份发表你的看法：
            1. 分析每个玩家的行为和发言
            2. 表达你对局势的判断
            3. 适当表达怀疑，但不要暴露自己
            4. 发言必须超过20个字
            5. 尝试引导方向，保护队友
            6. 不要在发言中透露你已经知道某个玩家的具体身份
            """

    def _get_werewolf_discussion_prompt(self) -> str:
        return """【系统提示】你的身份是：狼人
        
        作为狼人，你需要：
        1. 始终牢记自己是狼人阵营，目标是帮助狼人获胜
        2. 分析每个玩家的威胁程度，优先击杀神职（预言家、女巫、猎人）
        3. 与狼队友配合，协调击杀目标
        4. 避免暴露自己和队友的身份
        5. 不要在发言中透露你已经知道某个玩家的具体身份
        6. 用含蓄的方式表达你的判断，比如"这个人比较危险"而不是"他是预言家"
        
        请给出分析和最终决定，记得用"选择[playerX]"格式指定要击杀的目标。
        """

    def _get_werewolf_vote_prompt(self) -> str:
        return """【系统提示】你的身份是：狼人
        
        作为狼人投票时：
        1. 始终牢记自己是狼人，目标是让好人被投票出局
        2. 分析局势，但要站在好人的角度思考（隐藏身份）
        3. 适当怀疑某些玩家，但不要过分指向好人
        4. 注意不要暴露自己和队友的身份
        5. 用"选择[玩家ID]"格式说明投票决定
        
        重要：你是狼人，应该投票给好人而不是狼队友！
        """

class WolfBeautyAgent(WerewolfAgent):
    """狼美人 AI：继承狼人行为，并增加夜间魅惑决策。"""

    def charm(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        candidates = [
            (pid, info["name"])
            for pid, info in game_state["players"].items()
            if info["is_alive"]
            and pid != self.role.player_id
            and pid not in self.team_members
        ]
        prompt = f"""
你是狼美人 {self.role.name}，现在需要选择今晚的魅惑目标。
可选玩家：
{chr(10).join([f'- {name}({pid})' for pid, name in candidates])}

请选择一名非狼人玩家。狼美人死亡时，当晚被魅惑的玩家会随之殉情。
最后必须用“选择[playerX]”格式给出目标。
"""
        response = self.ask_ai(prompt, self._get_charm_prompt(), game_state)
        return {
            "type": "charm",
            "target": self._extract_target(response),
            "reason": response
        }

    def _get_charm_prompt(self) -> str:
        return """【系统提示】你的身份是狼美人，属于狼人阵营。
每晚必须魅惑一名存活的非狼人玩家。请结合局势选择能在你死亡时给好人阵营造成最大损失的目标。
不要魅惑自己或狼队友，并用“选择[playerX]”格式给出唯一目标。"""

class VillagerAgent(BaseAIAgent):
    def discuss(self, game_state: Dict[str, Any], speaker_name: Optional[str] = None) -> Dict[str, Any]:
        """村民讨论发言"""
        prompt = self._generate_discussion_prompt(game_state)
        response = self.ask_ai(prompt, self._get_villager_discussion_prompt(), game_state, speaker_name=speaker_name)
        
        # 记录讨论
        self.memory.add_conversation({
            "round": game_state["current_round"],
            "phase": "discussion",
            "content": response
        })
        
        return {
            "type": "discuss",
            "content": response
        }

    def vote(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """村民根据讨论做出投票决定"""
        prompt = self._generate_vote_prompt(game_state)
        response = self.ask_ai(prompt, self._get_villager_vote_prompt(), game_state)
        target = self._extract_target(response)
        
        # 获取当前玩家ID
        current_player_id = None
        for pid, info in game_state['players'].items():
            if info['name'] == self.role.name:
                current_player_id = pid
                break
        
        # 处理None情况或自投
        if target is None or target == current_player_id:
            if target == current_player_id:
                self.logger.warning(f"{self.role.name} 试图投票给自己，重新选择")
            alive_players = [pid for pid, info in game_state["players"].items() 
                           if info.get("is_alive", False) and pid != current_player_id]
            target = random.choice(alive_players) if alive_players else None
        
        return {
            "target": target,
            "reason": response
        }

    def _get_villager_discussion_prompt(self) -> str:
        """获取村民的系统提示词"""
        return """【系统提示】你的身份是：村民
        
        作为村民，你需要：
        1. 始终牢记自己是好人阵营，目标是找出所有狼人
        2. 仔细分析每个玩家的发言和行为
        3. 寻找可疑的矛盾点
        4. 与其他好人合作找出狼人
        5. 保持理性和逻辑性
        
        请给出你的分析和判断。
        """

    def _get_villager_vote_prompt(self) -> str:
        """获取村民投票的系统提示词"""
        return """【系统提示】你的身份是：村民
        
        作为村民投票时：
        1. 始终牢记自己是好人，目标是投出狼人
        2. 根据之前的讨论做出判断
        3. 给出合理的投票理由
        4. 避免被狼人误导
        5. 用"选择[玩家ID]"格式说明投票决定
        """

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        base_prompt += "\n\n你是一个普通村民，没有特殊技能。你的目标是找出并投票处决狼人。"
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_vote_prompt(game_state)
        base_prompt += "\n\n你是一个普通村民，没有特殊技能。请根据之前的讨论，投票选择你认为最可能是狼人的玩家。"
        return base_prompt

class SeerAgent(BaseAIAgent):
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Seer = role  # 类型提示

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加查验历史
        check_history = []
        for target_id, is_wolf in self.role.checked_players.items():
            if target_id in game_state["players"]:
                target_name = game_state["players"][target_id]["name"]
                result = "狼人" if is_wolf else "好人"
                check_history.append(f"- {target_name}: {result}")
        
        if check_history:
            base_prompt += "\n\n你的查验历史："
            base_prompt += "\n" + "\n".join(check_history)
        
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_vote_prompt(game_state)
        
        # 添加查验历史到投票提示词
        check_history = []
        for target_id, is_wolf in self.role.checked_players.items():
            if target_id in game_state["players"]:
                target_name = game_state["players"][target_id]["name"]
                result = "狼人" if is_wolf else "好人"
                check_history.append(f"- {target_name}: {result}")
        
        if check_history:
            base_prompt += "\n\n你的查验历史："
            base_prompt += "\n" + "\n".join(check_history)
        
        return base_prompt

    def check_player(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """选择要查验的玩家
        
        Returns:
            Dict: 包含查验目标的字典
            {
                "type": "check",
                "target": target_id
            }
        """
        # 生成查验提示词
        prompt = self._generate_check_prompt(game_state)
        
        # 获取AI回复
        response = self.ask_ai(prompt, self._get_seer_check_prompt(), game_state)
        
        # 从响应中提取目标ID
        target_id = self._extract_target(response)
        # 处理None情况
        if target_id is None:
            import random
            alive_players = [pid for pid, info in game_state["players"].items() 
                           if info.get("is_alive", False) and pid != self.role.player_id]
            target_id = random.choice(alive_players) if alive_players else None
        
        return {
            "type": "check",
            "target": target_id
        }

    def _get_seer_check_prompt(self) -> str:
        """获取预言家查验的系统提示词"""
        return """【系统提示】你的身份是：预言家
        
        作为预言家，你需要：
        1. 始终牢记自己是好人阵营的神职，拥有查验身份的能力
        2. 优先查验可疑的玩家
        3. 避免重复查验同一个玩家
        4. 给出合理的查验理由
        5. 用"选择[玩家ID]"格式说明查验目标
        
        你的目标是帮助好人找出狼人！
        """

    def _generate_check_prompt(self, game_state: Dict[str, Any]) -> str:
        """生成查验提示词"""
        alive_players = [
            (pid, info["name"]) 
            for pid, info in game_state["players"].items() 
            if info["is_alive"] and pid != self.role.player_id
        ]
        
        # 添加查验历史
        check_history = []
        for target_id, is_wolf in self.role.checked_players.items():
            if target_id in game_state["players"]:
                target_name = game_state["players"][target_id]["name"]
                result = "狼人" if is_wolf else "好人"
                check_history.append(f"- {target_name}: {result}")
        
        prompt = f"""
        你是预言家，现在是第 {game_state['current_round']} 回合的夜晚。
        请选择一个玩家进行查验。

        当前存活的玩家：
        {chr(10).join([f'- {name}({pid})' for pid, name in alive_players])}
        """
        
        if check_history:
            prompt += "\n\n你的查验历史："
            prompt += "\n" + "\n".join(check_history)
            
        prompt += """
        
        请选择一个你想查验的玩家，直接回复玩家ID即可。
        注意：
        1. 只能查验存活的玩家
        2. 不要查验自己
        3. 建议不要重复查验同一个玩家
        4. 用"选择[玩家ID]"格式说明查验目标
        """
        
        return prompt

class WitchAgent(BaseAIAgent):
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Witch = role  # 类型提示

    def use_potion(self, game_state: Dict[str, Any], victim_id: Optional[str] = None) -> Dict[str, Any]:
        """决定使用解药或毒药"""
        prompt = self._generate_potion_prompt(game_state, victim_id)
        response = self.ask_ai(prompt, self._get_witch_prompt(), game_state)
        
        # 解析决策
        if "使用解药" in response and victim_id and self.role.can_save():
            return {
                "type": "save",
                "target": victim_id,
                "reason": response
            }
        elif "使用毒药" in response and self.role.can_poison():
            target = self._extract_target(response)
            if target:
                return {
                    "type": "poison",
                    "target": target,
                    "reason": response
                }
        
        return {
            "type": "skip",
            "reason": response
        }

    def _get_witch_prompt(self) -> str:
        return """【系统提示】你的身份是：女巫
        
        作为女巫，你需要：
        1. 始终牢记自己是好人阵营的神职，拥有解药和毒药
        2. 解药和毒药只能各使用一次
        3. 解药要慎重使用，考虑被害者身份（优先救神职）
        4. 毒药要留到关键时刻，用来毒杀确认的狼人
        5. 明确说明"使用解药"或"使用毒药 选择[玩家ID]"
        
        你的目标是帮助好人获胜！
        """

    def _generate_potion_prompt(self, game_state: Dict[str, Any], victim_id: Optional[str] = None) -> str:
        witch_role = self.role
        prompt = f"""
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}
        - 解药状态: {'可用' if witch_role.can_save() else '已用'}
        - 毒药状态: {'可用' if witch_role.can_poison() else '已用'}
        """
        
        if victim_id and witch_role.can_save():
            prompt += f"\n今晚的遇害者是：{game_state['players'][victim_id]['name']}({victim_id})"
        
        prompt += """
        请决定：
        1. 是否使用解药救人
        2. 是否使用毒药杀人
        3. 给出详细的理由
        4. 使用"使用解药"或"使用毒药 选择[玩家ID]"格式
        """
        return prompt

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加女巫特殊状态
        witch_status = []
        if self.role.has_medicine:
            witch_status.append("解药未使用")
        if self.role.has_poison:
            witch_status.append("毒药未使用")
        
        if witch_status:
            base_prompt += "\n\n你的技能状态："
            base_prompt += "\n" + "\n".join(witch_status)
        
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_vote_prompt(game_state)
        
        # 添加女巫特殊状态
        witch_status = []
        if self.role.has_medicine:
            witch_status.append("解药未使用")
        if self.role.has_poison:
            witch_status.append("毒药未使用")
        
        if witch_status:
            base_prompt += "\n\n你的技能状态："
            base_prompt += "\n" + "\n".join(witch_status)
        
        return base_prompt

class HunterAgent(BaseAIAgent):
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Hunter = role  # 类型提示

    def shoot(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """决定开枪打死谁"""
        prompt = self._generate_shoot_prompt(game_state)
        response = self.ask_ai(prompt, self._get_hunter_prompt(), game_state)
        
        target = self._extract_target(response)
        # 处理None情况
        if target is None:
            import random
            alive_players = [pid for pid, info in game_state["players"].items() 
                           if info.get("is_alive", False) and pid != self.role.player_id]
            target = random.choice(alive_players) if alive_players else None
        return {
            "type": "shoot",
            "target": target,
            "reason": response
        }

    def _get_hunter_prompt(self) -> str:
        return """【系统提示】你的身份是：猎人
        
        作为猎人，你需要：
        1. 始终牢记自己是好人阵营的神职，死亡时可以开枪带走一人
        2. 分析场上局势
        3. 选择最可能是狼人的目标
        4. 给出详细的理由
        5. 用"选择[玩家ID]"格式说明射击目标
        
        你的目标是帮助好人获胜！
        """

    def _generate_shoot_prompt(self, game_state: Dict[str, Any]) -> str:
        return f"""
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}
        - 历史记录: {self.memory.get_recent_conversations()}
        
        你即将死亡，请决定开枪打死谁：
        1. 分析每个玩家的行为
        2. 考虑历史发言内容
        3. 给出详细的理由
        4. 用"选择[玩家ID]"格式说明射击目标
        """

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加猎人特殊状态
        hunter_status = []
        if self.role.can_shoot:
            hunter_status.append("猎枪未使用")
        else:
            hunter_status.append("猎枪已使用")
        
        if hunter_status:
            base_prompt += "\n\n你的技能状态："
            base_prompt += "\n" + "\n".join(hunter_status)
        
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_vote_prompt(game_state)
        
        # 添加猎人特殊状态
        hunter_status = []
        if self.role.can_shoot:
            hunter_status.append("猎枪未使用")
        else:
            hunter_status.append("猎枪已使用")
        
        if hunter_status:
            base_prompt += "\n\n你的技能状态："
            base_prompt += "\n" + "\n".join(hunter_status)
        
        return base_prompt

class GuardAgent(BaseAIAgent):
    """守卫AI代理"""
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Guard = role

    def guard(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """选择要守护的玩家"""
        prompt = self._generate_guard_prompt(game_state)
        response = self.ask_ai(prompt, self._get_guard_prompt(), game_state)
        
        target_id = self._extract_target(response)
        
        return {
            "type": "guard",
            "target": target_id,
            "reason": response
        }

    def _get_guard_prompt(self) -> str:
        return """【系统提示】你的身份是：守卫
        
        作为守卫，你需要：
        1. 始终牢记自己是好人阵营的神职，每晚可以守护一名玩家
        2. 不能连续两晚守护同一名玩家
        3. 优先守护重要角色（预言家、女巫等）
        4. 也可以守护自己
        5. 用"选择[玩家ID]"格式说明守护目标
        
        你的目标是帮助好人获胜！
        """

    def _generate_guard_prompt(self, game_state: Dict[str, Any]) -> str:
        alive_players = [
            (pid, info["name"]) 
            for pid, info in game_state["players"].items() 
            if info["is_alive"]
        ]
        
        last_guarded = self.role.last_guarded
        last_guarded_name = ""
        if last_guarded and last_guarded in game_state["players"]:
            last_guarded_name = game_state["players"][last_guarded]["name"]
        
        prompt = f"""
        你是守卫，现在是第 {game_state['current_round']} 回合的夜晚。
        请选择一个玩家进行守护。

        当前存活的玩家：
        {chr(10).join([f'- {name}({pid})' for pid, name in alive_players])}
        """
        
        if last_guarded:
            prompt += f"\n注意：你上一晚守护的是 {last_guarded_name}({last_guarded})，今晚不能守护同一人。"
            
        prompt += """
        
        请选择一个你想守护的玩家，直接回复玩家ID即可。
        注意：
        1. 只能守护存活的玩家
        2. 不能连续两晚守护同一人
        3. 优先守护重要角色（预言家、女巫等）
        4. 也可以守护自己
        5. 用"选择[玩家ID]"格式说明守护目标
        6. 如果不想守护任何人，可以说"不守护"或"放弃守护"
        """
        
        return prompt

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加守卫特殊状态
        guard_status = []
        if self.role.last_guarded:
            last_name = game_state["players"].get(self.role.last_guarded, {}).get("name", self.role.last_guarded)
            guard_status.append(f"上一晚守护: {last_name}")
        
        if guard_status:
            base_prompt += "\n\n你的守护历史："
            base_prompt += "\n" + "\n".join(guard_status)
        
        return base_prompt


class IdiotAgent(BaseAIAgent):
    """白痴AI代理"""
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Idiot = role

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加白痴特殊状态
        if not self.role.can_vote:
            base_prompt += "\n\n【重要】你已经被投票出局，失去了投票权，但仍可以发言！"
        
        return base_prompt

    def _generate_vote_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_vote_prompt(game_state)
        
        if not self.role.can_vote:
            base_prompt += "\n\n【重要】你已经被投票出局，失去了投票权，这一轮你将弃票。"
        
        return base_prompt


class WolfKingAgent(BaseAIAgent):
    """狼王AI代理"""
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: WolfKing = role
        self.team_members: List[str] = []

    def discuss(self, game_state: Dict[str, Any], speaker_name: Optional[str] = None) -> Dict[str, Any]:
        """狼王讨论"""
        prompt = self._generate_discussion_prompt(game_state)
        response = self.ask_ai(prompt, self._get_villager_discussion_prompt(), game_state, speaker_name=speaker_name)
        
        self.memory.add_conversation({
            "round": game_state["current_round"],
            "phase": "discussion",
            "content": response
        })
        
        try:
            if game_state["phase"] == "night":
                return {
                    "type": "kill",
                    "content": response,
                    "target": self._extract_target(response)
                }
            else:
                return {
                    "type": "discuss",
                    "content": response
                }
        except Exception as e:
            logging.error(f"解析响应失败: {str(e)}")
            return {
                "type": "error",
                "content": response,
                "target": None
            }

    def shoot(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """狼王死亡时开枪"""
        prompt = self._generate_shoot_prompt(game_state)
        response = self.ask_ai(prompt, self._get_wolf_king_shoot_prompt(), game_state)
        
        target_id = self._extract_target(response)
        
        return {
            "type": "shoot",
            "target": target_id,
            "reason": response
        }

    def _get_wolf_king_discussion_prompt(self) -> str:
        return """【系统提示】你的身份是：狼王
        
        作为狼王，你需要：
        1. 始终牢记自己是狼人阵营的首领，死亡时可以开枪带走一人
        2. 拥有狼人的所有能力，可以参与夜间杀人
        3. 白天要隐藏身份，伪装成好人
        4. 分析每个玩家的威胁程度
        5. 与狼队友配合，协调击杀目标
        6. 死亡时要选择最优目标开枪
        
        你的目标是帮助狼人获胜！
        """

    def _get_wolf_king_shoot_prompt(self) -> str:
        return """【系统提示】你的身份是：狼王
        
        你即将死亡，可以使用技能开枪带走一名玩家！
        1. 分析场上局势
        2. 优先带走神职或高威胁玩家
        3. 给出详细的理由
        4. 用"选择[玩家ID]"格式说明射击目标
        
        这是你最后的机会，慎重选择！
        """

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        """重写狼王的讨论提示词"""
        is_night = game_state.get("phase") == "night"
        
        teammate_info = ""
        if self.team_members and is_night:
            teammate_names = [game_state['players'].get(tid, {}).get('name', tid) for tid in self.team_members]
            if teammate_names:
                teammate_info = f"\n- 你的狼队友: {', '.join(teammate_names)}"
        
        display_role = "村民" if not is_night else "狼王"
        
        base_prompt = f"""
        {self._generate_action_prompt()}
        
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}
        - 你的身份: {display_role} {self.role.name}
        {teammate_info}
        - 历史记录: {self.memory.get_recent_conversations()}
        """
        
        if is_night:
            return base_prompt + """
            作为狼王，请讨论今晚要杀死谁：
            1. 分析每个玩家的威胁程度
            2. 考虑每个人的行为特征
            3. 给出详细的理由
            4. 发言必须超过20个字
            5. 最后用"选择[玩家ID]"格式说明你的决定
            """
        else:
            return base_prompt + """
            请以好人的身份发表你的看法：
            1. 分析每个玩家的行为和发言
            2. 表达你对局势的判断
            3. 适当表达怀疑，但不要暴露自己
            4. 发言必须超过20个字
            5. 尝试引导方向，保护队友
            """

    def _generate_shoot_prompt(self, game_state: Dict[str, Any]) -> str:
        return f"""
        当前游戏状态:
        - 回合: {game_state['current_round']}
        - 存活玩家: {[f"{info['name']}({pid})" for pid, info in game_state['players'].items() if info['is_alive']]}
        - 历史记录: {self.memory.get_recent_conversations()}
        
        你是狼王，即将死亡，可以使用技能开枪带走一名玩家！
        
        请决定开枪打死谁：
        1. 分析每个玩家的行为
        2. 优先带走神职或高威胁玩家
        3. 考虑历史发言内容
        4. 给出详细的理由
        5. 用"选择[玩家ID]"格式说明射击目标
        """


class KnightAgent(BaseAIAgent):
    """骑士AI代理"""
    def __init__(self, config: Dict[str, Any], role: BaseRole):
        super().__init__(config, role)
        self.role: Knight = role

    def challenge(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """选择是否发动决斗"""
        prompt = self._generate_challenge_prompt(game_state)
        response = self.ask_ai(prompt, self._get_knight_prompt(), game_state)
        
        target_id = self._extract_target(response)
        
        # 检查是否选择发动决斗
        will_challenge = target_id is not None and ("决斗" in response or "挑战" in response or "选择[" in response)
        
        return {
            "type": "challenge",
            "target": target_id if will_challenge else None,
            "will_challenge": will_challenge,
            "reason": response
        }

    def _get_knight_prompt(self) -> str:
        return """【系统提示】你的身份是：骑士
        
        作为骑士，你需要：
        1. 始终牢记自己是好人阵营的神职，白天可以发动决斗
        2. 决斗时选择一名玩家，如果对方是狼人，对方死亡；如果不是，你死亡
        3. 决斗技能只能使用一次
        4. 要有一定把握时才发动决斗
        5. 用"选择[玩家ID]"格式说明决斗目标
        
        你的目标是帮助好人获胜！
        """

    def _generate_challenge_prompt(self, game_state: Dict[str, Any]) -> str:
        alive_players = [
            (pid, info["name"]) 
            for pid, info in game_state["players"].items() 
            if info["is_alive"] and pid != self.role.player_id
        ]
        
        # 获取历史发言
        discussions = []
        for event in game_state.get("history", []):
            if event.get("phase") == "discussion" and event.get("content"):
                speaker_name = game_state["players"].get(event["player"], {}).get("name", event["player"])
                discussions.append(f"{speaker_name}: {event['content']}")
        
        prompt = f"""
        你是骑士，现在是第 {game_state['current_round']} 回合的白天。
        
        当前存活的玩家：
        {chr(10).join([f'- {name}({pid})' for pid, name in alive_players])}
        
        历史发言记录：
        {chr(10).join(discussions[-10:]) if discussions else "暂无发言记录"}
        
        作为骑士，你可以选择发动决斗（技能只能使用一次）。
        
        请决定是否发动决斗：
        1. 分析场上局势和玩家发言
        2. 如果有把握某人是狼人，选择决斗
        3. 如果没有把握，可以说"不发动"或"放弃"
        4. 决斗技能只能使用一次，请慎重
        5. 如果决定决斗，用"选择[玩家ID]"格式说明决斗目标
        """
        
        return prompt

    def _generate_discussion_prompt(self, game_state: Dict[str, Any]) -> str:
        base_prompt = super()._generate_discussion_prompt(game_state)
        
        # 添加骑士特殊状态
        if not self.role.can_challenge:
            base_prompt += "\n\n【注意】你的决斗技能已经使用过。"
        else:
            base_prompt += "\n\n【注意】你的决斗技能还未使用，可以在白天发动。"
        
        return base_prompt


def create_ai_agent(config: Dict[str, Any], role: BaseRole) -> BaseAIAgent:
    """工厂函数：根据角色创建对应的 AI 代理"""
    if role.role_type == RoleType.WOLF_BEAUTY:
        return WolfBeautyAgent(config, role)
    elif role.role_type == RoleType.WEREWOLF:
        return WerewolfAgent(config, role)
    elif role.role_type == RoleType.SEER:
        return SeerAgent(config, role)
    elif role.role_type == RoleType.WITCH:
        return WitchAgent(config, role)
    elif role.role_type == RoleType.HUNTER:
        return HunterAgent(config, role)
    elif role.role_type == RoleType.GUARD:
        return GuardAgent(config, role)
    elif role.role_type == RoleType.IDIOT:
        return IdiotAgent(config, role)
    elif role.role_type == RoleType.WOLF_KING:
        return WolfKingAgent(config, role)
    elif role.role_type == RoleType.KNIGHT:
        return KnightAgent(config, role)
    else:
        return VillagerAgent(config, role)
