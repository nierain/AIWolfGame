from typing import Dict, List, Optional
import time
import logging
from .roles import BaseRole, Werewolf, WolfBeauty, Villager, RoleType, Seer, Witch, Hunter, Guard, Idiot, WolfKing, Knight
from .ai_players import create_ai_agent, BaseAIAgent
import random
import re
from utils.logger import GameLogger, setup_logger
from datetime import datetime

class GameController:
    def __init__(self, config: Dict):
        """初始化游戏控制器
        
        Args:
            config: 游戏配置字典
            {
                'roles': {角色配置} 或 'players': {玩家配置} + 'role_counts': {角色数量},
                'game_settings': {游戏设置},
                'ai_players': {AI玩家配置}
                'delay': 延迟时间
            }
        """
        self.config = config
        self.players = {}  # 玩家ID -> Role对象
        self.ai_agents = {}  # 玩家ID -> AIAgent对象
        self.current_round = 1
        self.delay = config.get("delay", 1.0)  # 获取延迟设置，默认1秒
        
        self.game_state = {
            "current_round": self.current_round,
            "phase": "init",
            "players": {},  # 玩家状态信息
            "history": [],  # 游戏历史记录
            "alive_count": {"werewolf": 0, "villager": 0},  # 存活人数统计
            "vote_stats": {  # 投票统计
                "total_votes": 0,
                "invalid_votes": 0,
                "player_stats": {}
            },
            "start_time": datetime.now().isoformat(),  # 添加游戏开始时间
            "sheriff": None,  # 警长ID
            "sheriff_badge": True,  # 警徽是否存在
            "sheriff_candidates": [],  # 警长竞选候选人
            "wolf_explode_count": 0,  # 狼人自爆次数
            "guard_target": None,  # 守卫守护目标
            "last_guard_target": None  # 上一晚守卫守护目标
        }
        
        debug_mode = config.get("debug", False)
        self.logger = setup_logger(debug=debug_mode)

    def _random_assign_roles(self) -> Dict:
        """随机分配角色给玩家
        
        Returns:
            Dict: 角色分配结果 {player_id: role_type}
        """
        game_settings = self.config.get("game_settings", {})
        use_random = game_settings.get("random_roles", False)
        
        if not use_random:
            return None
        
        role_counts = self.config.get("role_counts", {})
        players_config = self.config.get("players", {})
        
        if not role_counts or not players_config:
            logging.warning("缺少 role_counts 或 players 配置，使用固定角色分配")
            return None
        
        player_ids = list(players_config.keys())
        role_list = []
        
        for role_type, count in role_counts.items():
            role_list.extend([role_type] * count)
        
        if len(role_list) != len(player_ids):
            raise ValueError(f"角色数量({len(role_list)})与玩家数量({len(player_ids)})不匹配")
        
        random.shuffle(role_list)
        
        assignments = {}
        for i, player_id in enumerate(player_ids):
            assignments[player_id] = role_list[i]
        
        logging.info(f"随机角色分配结果: {assignments}")
        return assignments

    def _log_role_recognition(self, player_id: str, target_id: str, guess_is_wolf: bool):
        """记录角色识别准确率"""
        actual_is_wolf = self.players[target_id].is_wolf()
        is_correct = guess_is_wolf == actual_is_wolf
        if hasattr(self.logger, 'log_role_recognition'):
            self.logger.log_role_recognition(player_id, is_correct)

    def _log_deception_attempt(self, wolf_id: str, is_successful: bool):
        """记录狼人欺骗成功率"""
        if hasattr(self.logger, 'log_deception_attempt'):
            self.logger.log_deception_attempt(wolf_id, is_successful)

    def _log_vote(self, voter_id: str, target_id: str):
        """记录投票情况
        
        Args:
            voter_id: 投票者ID
            target_id: 目标ID
        """
        # 获取投票者和目标的角色
        voter_role = self.players[voter_id]
        target_role = self.players[target_id]
        
        # 计算投票正确性 - 如果好人投票给狼人或狼人投票给好人，视为正确投票
        is_correct = False
        if voter_role.is_wolf() and not target_role.is_wolf():
            # 狼人投给了好人 - 策略性正确(保护狼队友)
            is_correct = True
        elif not voter_role.is_wolf() and target_role.is_wolf():
            # 好人投给了狼人 - 判断正确
            is_correct = True
            
        # 记录投票准确率指标
        if hasattr(self.logger, 'log_vote'):
            self.logger.log_vote(voter_id, target_id, is_correct)
        
        # 同时记录到游戏状态中
        if "votes" not in self.game_state:
            self.game_state["votes"] = []
            
        self.game_state["votes"].append({
            "round": self.current_round,
            "voter": voter_id,
            "target": target_id,
            "is_correct": is_correct,
            "voter_role": voter_role.role_type.value,
            "target_role": target_role.role_type.value
        })

    def _log_communication(self, player_id: str, message_id: str, influenced_others: bool):
        """记录沟通效果"""
        if hasattr(self.logger, 'log_communication'):
            self.logger.log_communication(player_id, message_id, influenced_others)

    def _log_survival(self, player_id: str):
        """记录生存率"""
        if hasattr(self.logger, 'log_survival'):
            self.logger.log_survival(player_id, self.current_round, self.config.get("total_rounds", 100))

    def _log_ability_usage(self, player_id: str, ability_type: str, is_correct: bool):
        """记录能力使用准确率"""
        if hasattr(self.logger, 'log_ability_usage'):
            self.logger.log_ability_usage(player_id, ability_type, is_correct)

    def _log_invalid_vote(self, player_id: str, reason: str):
        """记录无效投票
        
        Args:
            player_id: 投票者ID
            reason: 无效原因
        """
        if player_id not in self.game_state["vote_stats"]["player_stats"]:
            self.game_state["vote_stats"]["player_stats"][player_id] = {
                "total_votes": 0,
                "invalid_votes": 0,
                "invalid_reasons": []
            }
        
        stats = self.game_state["vote_stats"]["player_stats"][player_id]
        stats["total_votes"] += 1
        stats["invalid_votes"] += 1
        stats["invalid_reasons"].append({
            "round": self.current_round,
            "reason": reason
        })
        
        self.game_state["vote_stats"]["total_votes"] += 1
        self.game_state["vote_stats"]["invalid_votes"] += 1

    def _log_valid_vote(self, player_id: str):
        """记录有效投票"""
        if player_id not in self.game_state["vote_stats"]["player_stats"]:
            self.game_state["vote_stats"]["player_stats"][player_id] = {
                "total_votes": 0,
                "invalid_votes": 0,
                "invalid_reasons": []
            }
        
        self.game_state["vote_stats"]["player_stats"][player_id]["total_votes"] += 1
        self.game_state["vote_stats"]["total_votes"] += 1

    def initialize_game(self) -> None:
        """初始化游戏，创建角色和AI代理"""
        random_assignments = self._random_assign_roles()
        
        if random_assignments:
            players_config = self.config.get("players", {})
            model_assignments = self.config.get("model_assignments", {})
            
            for player_id, role_type in random_assignments.items():
                info = players_config[player_id]
                name = info["name"]
                
                if role_type == "werewolf":
                    role = Werewolf(player_id, name)
                    self.game_state["alive_count"]["werewolf"] += 1
                elif role_type == "wolf_beauty":
                    role = WolfBeauty(player_id, name)
                    self.game_state["alive_count"]["werewolf"] += 1
                elif role_type == "seer":
                    role = Seer(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                elif role_type == "witch":
                    role = Witch(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                elif role_type == "hunter":
                    role = Hunter(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                elif role_type == "guard":
                    role = Guard(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                elif role_type == "idiot":
                    role = Idiot(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                elif role_type == "wolf_king":
                    role = WolfKing(player_id, name)
                    self.game_state["alive_count"]["werewolf"] += 1
                elif role_type == "knight":
                    role = Knight(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                else:
                    role = Villager(player_id, name)
                    self.game_state["alive_count"]["villager"] += 1
                
                self.players[player_id] = role
                self.game_state["players"][player_id] = {
                    "name": name,
                    "is_alive": True,
                    "role": role_type,
                    "ai_model": model_assignments.get(player_id, "unknown")
                }
                
                ai_type = model_assignments.get(player_id)
                if not ai_type:
                    logging.warning(f"玩家 {player_id} 没有指定AI类型，使用默认配置")
                    ai_type = "default"
                
                if ai_type not in self.config["ai_players"]:
                    raise ValueError(f"未知的AI类型: {ai_type}")
                
                ai_config = self.config["ai_players"][ai_type]
                self.ai_agents[player_id] = create_ai_agent(ai_config, role)
        else:
            for role_type, players in self.config["roles"].items():
                for player_id, info in players.items():
                    if role_type == "werewolf":
                        role = Werewolf(player_id, info["name"])
                        self.game_state["alive_count"]["werewolf"] += 1
                    elif role_type == "wolf_beauty":
                        role = WolfBeauty(player_id, info["name"])
                        self.game_state["alive_count"]["werewolf"] += 1
                    elif role_type == "seer":
                        role = Seer(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    elif role_type == "witch":
                        role = Witch(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    elif role_type == "hunter":
                        role = Hunter(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    elif role_type == "guard":
                        role = Guard(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    elif role_type == "idiot":
                        role = Idiot(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    elif role_type == "wolf_king":
                        role = WolfKing(player_id, info["name"])
                        self.game_state["alive_count"]["werewolf"] += 1
                    elif role_type == "knight":
                        role = Knight(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    else:
                        role = Villager(player_id, info["name"])
                        self.game_state["alive_count"]["villager"] += 1
                    
                    self.players[player_id] = role
                    self.game_state["players"][player_id] = {
                        "name": info["name"],
                        "is_alive": True,
                        "role": role_type,
                        "ai_model": info.get("ai_type", "unknown")
                    }
                    
                    ai_type = info.get("ai_type")
                    if not ai_type:
                        logging.warning(f"玩家 {player_id} 没有指定AI类型，使用默认配置")
                        ai_type = "default"
                    
                    if ai_type not in self.config["ai_players"]:
                        raise ValueError(f"未知的AI类型: {ai_type}")
                    
                    ai_config = self.config["ai_players"][ai_type]
                    self.ai_agents[player_id] = create_ai_agent(ai_config, role)

        wolf_players = [pid for pid, role in self.players.items() if role.is_wolf()]
        for wolf_id in wolf_players:
            agent = self.ai_agents[wolf_id]
            if hasattr(agent, 'team_members'):
                agent.team_members = [p for p in wolf_players if p != wolf_id]
        
        if random_assignments:
            print("\n=== 本局角色分配（随机）===")
            for player_id, role_type in random_assignments.items():
                player_info = self.config.get("players", {}).get(player_id, {})
                model = self.config.get("model_assignments", {}).get(player_id, "unknown")
                print(f"{player_info.get('name', player_id)}({player_id}): {role_type} - 模型: {model}")
            print("")

    def run_game(self) -> None:
        """运行游戏主循环"""
        self.initialize_game()
        
        while not self.check_game_over():
            self.game_state["current_round"] = self.current_round
            print(f"\n=== 第 {self.current_round} 回合 ===")
            
            # 夜晚阶段
            self.night_phase()
            if self.check_game_over():
                break
                
            # 白天阶段
            self.day_phase()
            
            self.current_round += 1
            time.sleep(self.delay)  # 回合间延迟
        
        self.announce_winner()

    def night_phase(self) -> None:
        """夜晚阶段：狼人杀人，神职技能"""
        print("\n=== 夜晚降临 ===")
        self.game_state["phase"] = "night"
        time.sleep(self.delay)

        # 获取存活的狼人和神职玩家
        wolves = [pid for pid, role in self.players.items() 
                 if role.is_wolf() and role.is_alive]
        seers = [pid for pid, role in self.players.items() 
                if isinstance(role, Seer) and role.is_alive]
        witches = [pid for pid, role in self.players.items() 
                  if isinstance(role, Witch) and role.is_alive]
        hunters = [pid for pid, role in self.players.items()
                  if isinstance(role, Hunter) and role.is_alive]
        guards = [pid for pid, role in self.players.items()
                  if isinstance(role, Guard) and role.is_alive]
        wolf_beauties = [pid for pid, role in self.players.items()
                         if isinstance(role, WolfBeauty) and role.is_alive]
        
        victim_id = None  # 狼人的目标
        saved_by_witch = False  # 是否被女巫救活
        poisoned_by_witch = None  # 女巫毒死的玩家
        guarded_target = None  # 守卫守护的目标

        # 狼人行动
        if wolves:
            print("\n狼人们正在商讨...")
            time.sleep(self.delay)
            
            # 狼人讨论
            wolf_opinions = []
            wolf_targets = []  # 收集所有狼人的目标
            
            for wolf_id in wolves:
                agent = self.ai_agents[wolf_id]
                result = agent.discuss(self.game_state, speaker_name=self.players[wolf_id].name)
                
                if result["type"] == "kill":
                    
                    # print(result["content"])
                    target = result.get("target")
                    if target:
                        wolf_targets.append(target)
                    wolf_opinions.append({
                        "wolf": self.players[wolf_id].name,
                        "opinion": result["content"],
                        "target": target
                    })
                
                time.sleep(self.delay)
            
            # 第一回合只确认身份，不杀人
            if self.current_round == 1:
                print("\n第一个夜晚，狼人互相确认身份")
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "night",
                    "event": "wolf_identify",
                    "opinions": wolf_opinions
                })
                victim_id = None
            else:
                # 检查是否有指刀目标（狼人自爆时指定）
                designated_target = self.game_state.get("designated_target")
                if designated_target and designated_target in self.players and self.players[designated_target].is_alive:
                    victim_id = designated_target
                    print(f"\n狼人按照指刀，选择击杀 {self.players[victim_id].name}")
                    # 清除指刀目标
                    del self.game_state["designated_target"]
                elif wolf_targets:
                    # 如果有多个狼人，随机选择一个目标
                    victim_id = random.choice(wolf_targets)
                    self._log_deception_attempt(wolves[0], True)

        # 狼美人每晚单独选择魅惑目标
        for beauty_id in wolf_beauties:
            beauty = self.players[beauty_id]
            beauty.charmed_player_id = None
            result = self.ai_agents[beauty_id].charm(self.game_state)
            target_id = result.get("target")
            if (target_id in self.players
                    and self.players[target_id].is_alive
                    and target_id != beauty_id
                    and not self.players[target_id].is_wolf()):
                beauty.charm(target_id)
                print(f"\n狼美人 {beauty.name} 魅惑了 {self.players[target_id].name}")
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "night",
                    "event": "wolf_beauty_charm",
                    "wolf_beauty": beauty_id,
                    "target": target_id
                })
            else:
                print(f"\n狼美人 {beauty.name} 的魅惑目标无效，本晚未能魅惑")
        
        # 预言家行动
        if seers:
            print("\n预言家正在行动...")
            for seer_id in seers:
                agent = self.ai_agents[seer_id]
                seer = self.players[seer_id]
                result = agent.check_player(self.game_state)
                
                if result["type"] == "check" and result["target"]:
                    target_id = result["target"]
                    
                    # 检查是否可以查验
                    if seer.can_check(target_id):
                        target_role = self.players[target_id]
                        is_wolf = target_role.is_wolf()
                        # 记录查验结果
                        seer.check_role(target_id, is_wolf)
                        # 记录预言家的查验准确率
                        self._log_ability_usage(seer_id, "查验", True)
                        self._log_role_recognition(seer_id, target_id, is_wolf)
                        
                        print(f"\n{self.players[seer_id].name} 查验了 {self.players[target_id].name}")
                        print(f"查验结果：{'是狼人' if is_wolf else '是好人'}")
                        
                        # 记录查验结果到游戏状态
                        if "seer_checks" not in self.game_state:
                            self.game_state["seer_checks"] = {}
                        if seer_id not in self.game_state["seer_checks"]:
                            self.game_state["seer_checks"][seer_id] = {}
                        self.game_state["seer_checks"][seer_id][target_id] = is_wolf
                        
                        # 记录查验结果
                        self.game_state["history"].append({
                            "round": self.current_round,
                            "phase": "night",
                            "event": "seer_check",
                            "seer": seer_id,
                            "target": target_id,
                            "is_wolf": is_wolf
                        })
                    else:
                        print(f"\n{self.players[seer_id].name} 选择的查验目标无效")
                
                time.sleep(self.delay)
        
        # 如果有人被狼人杀死
        if victim_id:
            print(f"\n今晚，{self.players[victim_id].name} 被狼人袭击了...")
            # 女巫行动
            if witches:
                print("\n女巫正在行动...")
                for witch_id in witches:
                    agent = self.ai_agents[witch_id]
                    witch = self.players[witch_id]
                    result = agent.use_potion(self.game_state, victim_id)
                    
                    if result["type"] == "save":
                        # 检查是否可以使用解药
                        if witch.can_save(is_first_night=self.current_round == 1):
                            saved_by_witch = True
                            witch.use_medicine()
                            # 记录女巫的救人
                            self._log_ability_usage(witch_id, "救人", True)
                            print(f"\n{self.players[witch_id].name} 使用了解药，救活了 {self.players[victim_id].name}")
                        else:
                            if not witch.has_medicine:
                                print(f"\n{self.players[witch_id].name} 的解药已经用完了")
                            elif witch.used_medicine_this_round:
                                print(f"\n{self.players[witch_id].name} 本回合已经使用过解药")
                            else:
                                print(f"\n{self.players[witch_id].name} 选择不使用解药")
                    elif result["type"] == "poison" and result["target"]:
                        # 检查是否可以使用毒药
                        if witch.can_poison(is_first_night=self.current_round == 1):
                            poisoned_by_witch = result["target"]
                            witch.use_poison()
                            # 记录女巫的毒人
                            self._log_ability_usage(witch_id, "毒人", True)
                            print(f"\n{self.players[witch_id].name} 使用了毒药")
                            if self.current_round == 1:
                                print("【系统提示】第一晚使用毒药可能不是最佳选择")
                        else:
                            if not witch.has_poison:
                                print(f"\n{self.players[witch_id].name} 的毒药已经用完了")
                            else:
                                print(f"\n{self.players[witch_id].name} 选择不使用毒药")
                    
                    # 记录女巫行动
                    self.game_state["history"].append({
                        "round": self.current_round,
                        "phase": "night",
                        "event": "witch_action",
                        "witch": witch_id,
                        "action_type": result["type"],
                        "target": result["target"] if "target" in result else None,
                        "success": saved_by_witch or poisoned_by_witch is not None
                    })
                    
                    # 重置女巫的回合状态
                    witch.reset_round()
                    
                    time.sleep(self.delay)
        
        # 守卫行动
        if guards:
            print("\n守卫正在行动...")
            for guard_id in guards:
                agent = self.ai_agents[guard_id]
                guard = self.players[guard_id]
                result = agent.guard(self.game_state)
                
                if result["type"] == "guard" and result["target"]:
                    target_id = result["target"]
                    
                    # 检查是否可以守护
                    same_guard_same_target = self.config.get("rules", {}).get("same_guard_same_target", False)
                    if guard.can_guard_target(target_id, same_guard_same_target):
                        guarded_target = target_id
                        guard.guard_player(target_id)
                        self._log_ability_usage(guard_id, "守护", True)
                        print(f"\n{self.players[guard_id].name} 守护了 {self.players[target_id].name}")
                    else:
                        if target_id == guard.last_guarded:
                            print(f"\n{self.players[guard_id].name} 不能连续两晚守护同一人")
                        else:
                            print(f"\n{self.players[guard_id].name} 选择的守护目标无效")
                else:
                    print(f"\n{self.players[guard_id].name} 选择不守护")
                
                # 记录守卫行动
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "night",
                    "event": "guard_action",
                    "guard": guard_id,
                    "target": result.get("target") if "target" in result else None,
                    "success": guarded_target is not None
                })
                
                time.sleep(self.delay)
        
        # 处理夜晚死亡
        night_deaths = []
        
        # 处理狼人杀人
        if victim_id and not saved_by_witch:
            # 检查是否被守卫守护
            if guarded_target == victim_id:
                print(f"\n{self.players[victim_id].name} 被守卫守护，逃过一劫")
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "night",
                    "event": "guard_save",
                    "victim": victim_id,
                    "guard": guards[0] if guards else None
                })
            else:
                night_deaths.append((victim_id, "被狼人杀死"))
        
        # 处理女巫毒人
        if poisoned_by_witch:
            # 检查是否被守卫守护（同守同救规则）
            same_guard_same_target = self.config.get("rules", {}).get("same_guard_same_target", False)
            if same_guard_same_target and guarded_target == poisoned_by_witch:
                print(f"\n{self.players[poisoned_by_witch].name} 被守卫守护，毒药无效")
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "night",
                    "event": "guard_block_poison",
                    "victim": poisoned_by_witch,
                    "guard": guards[0] if guards else None
                })
            else:
                night_deaths.append((poisoned_by_witch, "被毒死"))
        
        # 更新守卫的上一晚守护目标
        self.game_state["last_guard_target"] = guarded_target
        
        # 执行死亡
        for player_id, reason in night_deaths:
            self._handle_death(player_id, reason)
            
            # 如果死者是猎人，确认其死亡状态
            if isinstance(self.players[player_id], Hunter):
                hunter = self.players[player_id]
                hunter.confirm_death()
                
                # 让猎人开枪
                if hunter.can_use_gun():
                    print(f"\n{hunter.name} 倒下的瞬间，抽出了猎枪...")
                    agent = self.ai_agents[player_id]
                    result = agent.shoot(self.game_state)
                    
                    if result["type"] == "shoot" and result["target"]:
                        target_id = result["target"]
                        if target_id in self.players and self.players[target_id].is_alive:
                            hunter.use_gun()
                            self._log_ability_usage(player_id, "开枪", True)
                            print(f"\n{hunter.name} 对准了 {self.players[target_id].name}...")
                            time.sleep(self.delay)
                            print("砰！一声枪响...")
                            time.sleep(self.delay)
                            print(f"{self.players[target_id].name} 被猎人射杀")
                            self._handle_death(target_id, "被猎人射杀")
                        else:
                            print(f"\n{hunter.name} 的目标无效，猎枪未能发射")
                    else:
                        print(f"\n{hunter.name} 没有开枪，带着遗憾离开了")
                else:
                    if not hunter.can_shoot:
                        print(f"\n{hunter.name} 已经开过枪了")
                    else:
                        print(f"\n{hunter.name} 没有机会开枪就离开了")

    def _handle_death(self, player_id: str, reason: str) -> None:
        """处理玩家死亡"""
        role = self.players[player_id]
        if not role.is_alive:
            return
        role.is_alive = False
        
        # 更新存活计数
        if role.is_wolf():
            self.game_state["alive_count"]["werewolf"] -= 1
        else:
            self.game_state["alive_count"]["villager"] -= 1
        
        # 更新游戏状态
        self.game_state["players"][player_id]["is_alive"] = False
        
        # 记录死亡信息
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": "night" if self.game_state["phase"] == "night" else "day",
            "event": "death",
            "player": player_id,
            "reason": reason
        })
        
        print(f"\n{role.name} {reason}")
        
        # 记录生存率
        self._log_survival(player_id)

        if isinstance(role, WolfBeauty):
            self._handle_wolf_beauty_martyrdom(role)

    def _handle_wolf_beauty_martyrdom(self, wolf_beauty: WolfBeauty) -> None:
        """狼美人死亡时，让当晚仍存活的魅惑目标殉情。"""
        target_id = wolf_beauty.charmed_player_id
        if not target_id or target_id not in self.players:
            return
        target = self.players[target_id]
        if not target.is_alive:
            return

        print(f"\n{target.name} 因狼美人 {wolf_beauty.name} 死亡而殉情")
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": self.game_state["phase"],
            "event": "wolf_beauty_martyrdom",
            "wolf_beauty": wolf_beauty.player_id,
            "target": target_id
        })
        if self.game_state["phase"] == "night":
            self._handle_death(target_id, "因狼美人死亡而殉情")
        else:
            self.kill_player(target_id, "因狼美人死亡而殉情", allow_last_words=False)

    def day_phase(self) -> None:
        """白天阶段：玩家轮流发言后进行投票"""
        print("\n=== 天亮了 ===")
        self.game_state["phase"] = "day"
        time.sleep(self.delay)

        # 第一天的特殊处理：警长竞选
        if self.current_round == 1 and self.config.get("rules", {}).get("enable_sheriff", True):
            # 检查是否有狼人自爆（吞警徽）
            if self.game_state.get("wolf_exploded_first_day", False):
                print("\n【系统】由于狼人自爆，警徽被吞，跳过警长竞选")
                self.game_state["sheriff_badge"] = False
            else:
                # 进行警长竞选
                if self.sheriff_election_phase():
                    # 警长竞选成功，进行警长发言
                    self.sheriff_speech_phase()
                    return  # 第一天警长竞选后直接进入夜晚

        # 轮流发言
        self.discussion_phase()

        # 骑士可以在发言后、投票前发动一次决斗
        if self.knight_challenge_phase():
            return
        
        # 检查是否有狼人自爆
        if self.wolf_explode_phase():
            # 狼人自爆，跳过投票直接进入夜晚
            return
        
        # 投票环节
        self.voting_phase()

    def knight_challenge_phase(self) -> bool:
        """处理骑士白天决斗；发动决斗后立即结束当天。"""
        knights = [
            (pid, role) for pid, role in self.players.items()
            if isinstance(role, Knight) and role.is_alive and role.can_challenge
        ]
        for knight_id, knight in knights:
            result = self.ai_agents[knight_id].challenge(self.game_state)
            target_id = result.get("target") if result.get("will_challenge") else None
            if not target_id:
                continue
            if (target_id not in self.players
                    or target_id == knight_id
                    or not self.players[target_id].is_alive):
                print(f"\n骑士 {knight.name} 的决斗目标无效")
                continue

            knight.challenge(target_id)
            target = self.players[target_id]
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "day",
                "event": "knight_challenge",
                "knight": knight_id,
                "target": target_id,
                "target_is_wolf": target.is_wolf()
            })
            if target.is_wolf():
                print(f"\n骑士 {knight.name} 决斗成功，{target.name} 是狼人！")
                self.kill_player(target_id, "骑士决斗出局", allow_last_words=False)
            else:
                print(f"\n骑士 {knight.name} 决斗失败，{target.name} 是好人！")
                self.kill_player(knight_id, "决斗失败出局", allow_last_words=False)
            return True
        return False

    def _validate_speech(self, speech: str) -> bool:
        """验证发言是否符合要求"""
        # 移除动作描写【】中的内容后检查发言长度
        clean_speech = re.sub(r'【.*?】', '', speech)
        return len(clean_speech) >= 20

    def discussion_phase(self) -> None:
        """玩家轮流发言阶段"""
        print("\n=== 开始轮流发言 ===")
        time.sleep(self.delay)
        
        # 记录所有发言
        round_speeches = []
        
        # 第一轮发言
        alive_players = [pid for pid, role in self.players.items() if role.is_alive]
        print("\n【第一轮发言】")
        for player_id in alive_players:
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # 检查玩家是否有发言权
            if not role.is_alive:
                continue
            
            result = agent.discuss(self.game_state, speaker_name=role.name)
            
            # 处理不同类型的返回结果
            if isinstance(result, dict):
                speech = result.get("content", "")
            else:
                speech = result
            
            # 验证发言长度
            if not self._validate_speech(speech):
                logging.warning(f"{role.name} 的发言太短，要求重新发言")
                continue
            
            
            
            # 记录发言
            message_id = f"{self.current_round}_{player_id}_{len(round_speeches)}"
            round_speeches.append({
                "player": role.name,
                "role": role.role_type.value,
                "content": speech,
                "message_id": message_id
            })
            
            # 评估发言的影响力
            influenced_others = self._evaluate_speech_influence(speech, player_id)
            self._log_communication(player_id, message_id, influenced_others)
            
            # 更新游戏状态
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "discussion",
                "player": player_id,
                "content": speech
            })
            
            time.sleep(self.delay)
        
        # 更新游戏状态，加入当前讨论记录
        self.game_state["current_discussion"] = round_speeches
        
        # 第二轮发言（补充发言）
        print("\n【第二轮发言】")
        for player_id in alive_players:
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # 检查玩家是否有发言权
            if not role.is_alive:
                continue
            
            print(f"\n{role.name} 要补充发言吗？")
            result = agent.discuss(self.game_state, speaker_name=role.name)
            
            # 处理不同类型的返回结果
            if isinstance(result, dict):
                speech = result.get("content", "")
            else:
                speech = result
            
            if len(speech) > 50:  # 如果有实质性的补充
                
                round_speeches.append({
                    "player": role.name,
                    "role": role.role_type.value,
                    "content": speech
                })
                
                # 更新游戏状态
                self.game_state["history"].append({
                    "round": self.current_round,
                    "phase": "discussion",
                    "player": player_id,
                    "content": speech
                })
            else:
                print("无补充发言")
            
            time.sleep(self.delay)
        
        # 记录本轮所有讨论
        if hasattr(self.logger, 'log_round_discussion'):
            self.logger.log_round_discussion(self.current_round, round_speeches)

    def _evaluate_speech_influence(self, speech: str, speaker_id: str) -> bool:
        """评估发言的影响力
        
        通过分析发言内容和其他玩家的反应来判断发言是否有影响力
        """
        # 基本规则：
        # 1. 发言包含具体的分析和推理
        # 2. 提供了新的信息或视角
        # 3. 引起了其他玩家的回应
        has_analysis = len(re.findall(r'我认为|我觉得|我分析|根据|因为|所以', speech)) > 0
        has_new_info = len(re.findall(r'发现|注意到|观察到|怀疑|证据', speech)) > 0
        is_logical = len(re.findall(r'如果|那么|因此|证明|说明', speech)) > 0
        
        # 发言质量评分
        score = 0
        if has_analysis: score += 1
        if has_new_info: score += 1
        if is_logical: score += 1
        if len(speech) > 100: score += 1  # 较长的发言通常包含更多信息
        
        return score >= 2  # 得分达到2分以上认为是有影响力的发言

    def voting_phase(self, revote: bool = False, tied_players: List[str] = None) -> None:
        """投票环节
        
        Args:
            revote: 是否是重新投票
            tied_players: 平票的玩家ID列表（仅在重新投票时使用）
        """
        if revote:
            print("\n=== 重新投票阶段 ===")
        else:
            print("\n=== 开始投票 ===")
        print("\n请各位玩家依次进行投票...")
        time.sleep(self.delay)
        
        votes = {}
        vote_details = []
        
        # 只有存活玩家才能投票
        alive_players = [pid for pid, role in self.players.items() if role.is_alive]
        
        # 显示存活玩家列表
        print("\n当前存活玩家：")
        for pid in alive_players:
            print(f"- {self.players[pid].name} (ID: {pid})")
        print("\n开始投票...")
        
        # 获取本轮讨论内容（包括补充发言）
        current_round_discussions = []
        for event in self.game_state["history"]:
            if (event.get("round") == self.current_round and 
                event.get("phase") in ["discussion", "tiebreaker_speech"] and 
                event.get("content")):
                current_round_discussions.append({
                    "player": self.players[event["player"]].name,
                    "content": event["content"]
                })
        
        for player_id in alive_players:
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # 再次检查玩家是否存活
            if not role.is_alive:
                continue
            
            # 最多尝试3次投票
            max_attempts = 3
            current_attempt = 0
            valid_vote = False
            
            while not valid_vote and current_attempt < max_attempts:
                current_attempt += 1
                
                if current_attempt == 1:
                    print(f"\n轮到 {role.name} 投票...")
                else:
                    print(f"\n{role.name} 第 {current_attempt} 次尝试投票...")
                
                # 为AI提供投票提示和上下文
                vote_context = {
                    "type": "vote_context",
                    "current_round": self.current_round,
                    "voter": {
                        "id": player_id,
                        "name": role.name
                    },
                    "alive_players": [
                        {
                            "id": pid,
                            "name": self.players[pid].name
                        }
                        for pid in alive_players if pid != player_id
                    ],
                    "discussions": current_round_discussions,
                    "retry_count": current_attempt,
                    "is_revote": revote,  # 标记是否是重新投票
                    "tied_players": tied_players if revote else None,  # 平票玩家列表
                    "message": f"请根据本轮讨论内容进行投票，注意：\n"
                             f"1. 不能投票给自己 ({player_id})\n"
                             f"2. 只能投票给存活的玩家\n"
                             f"3. 必须使用正确的玩家ID格式\n" +
                             (f"4. 这是平票后的重新投票，只能投给平票的玩家：{', '.join([self.players[pid].name for pid in tied_players])}\n" if revote else "") +
                             f"\n本轮讨论内容：\n" +
                             "\n".join([f"{disc['player']}: {disc['content']}" 
                                      for disc in current_round_discussions]) +
                             f"\n\n当前存活玩家：\n" +
                             "\n".join([f"- {self.players[pid].name} (ID: {pid})" 
                                      for pid in alive_players if pid != player_id])
                }
                self.game_state["vote_context"] = vote_context
                
                # 获取投票目标和投票理由
                vote_result = agent.vote(self.game_state)
                target_id = vote_result.get("target")
                reason = vote_result.get("reason", "没有给出具体理由")
                
                # 检查是否弃票
                if target_id is None:
                    valid_vote = True
                    print(f"{role.name} 选择弃票")
                    print(f"弃票理由：{reason}")
                    vote_detail = {
                        "voter": player_id,
                        "voter_name": role.name,
                        "voter_role": role.role_type.value,
                        "target": None,
                        "target_name": "弃票",
                        "reason": reason,
                        "attempts": current_attempt
                    }
                    vote_details.append(vote_detail)
                    # 记录弃票
                    self._log_valid_vote(player_id)
                # 验证投票目标的有效性
                elif target_id:
                    if target_id == player_id:
                        print(f"【错误】不能投票给自己")
                        self._log_invalid_vote(player_id, "自投")
                    elif target_id not in self.players:
                        print(f"【错误】目标ID {target_id} 不存在")
                        self._log_invalid_vote(player_id, "目标ID不存在")
                    elif not self.players[target_id].is_alive:
                        print(f"【错误】目标玩家 {self.players[target_id].name} 已经死亡")
                        self._log_invalid_vote(player_id, "目标已死亡")
                    elif revote and tied_players and target_id not in tied_players:
                        print(f"【错误】重新投票时只能投给平票的玩家")
                        self._log_invalid_vote(player_id, "非平票玩家")
                    else:
                        valid_vote = True
                        self._log_valid_vote(player_id)
                        votes[target_id] = votes.get(target_id, 0) + 1
                        vote_detail = {
                            "voter": player_id,
                            "voter_name": role.name,
                            "voter_role": role.role_type.value,
                            "target": target_id,
                            "target_name": self.players[target_id].name,
                            "reason": reason,
                            "attempts": current_attempt
                        }
                        print(f"{role.name} 投票给了 {self.players[target_id].name}")
                        print(f"投票理由：{reason}")
                        vote_details.append(vote_detail)
                        
                        # 记录投票准确率
                        self._log_vote(player_id, target_id)
                else:
                    print(f"【错误】未能识别有效的投票目标")
                    self._log_invalid_vote(player_id, "无效的投票格式")
            
            # 如果三次尝试后仍未有效投票，记录为随机投票
            if not valid_vote:
                self._log_invalid_vote(player_id, "三次尝试失败，随机投票")
                possible_targets = [pid for pid in alive_players if pid != player_id]
                if possible_targets:
                    target_id = random.choice(possible_targets)
                    votes[target_id] = votes.get(target_id, 0) + 1
                    print(f"\n【系统】{role.name} 三次投票均无效")
                    print(f"【系统】随机指定投票给 {self.players[target_id].name}")
                    vote_detail = {
                        "voter": player_id,
                        "voter_name": role.name,
                        "voter_role": role.role_type.value,
                        "target": target_id,
                        "target_name": self.players[target_id].name,
                        "reason": "三次投票无效，系统随机指定",
                        "attempts": current_attempt
                    }
                    vote_details.append(vote_detail)
                    self._log_vote(player_id, target_id)
                else:
                    logging.warning(f"{role.name} 无法进行有效投票：没有合适的目标")
            
            # 清除投票上下文
            if "vote_context" in self.game_state:
                del self.game_state["vote_context"]
            
            time.sleep(self.delay)

        # 统计投票结果
        # 统计弃票玩家
        abstained_players = [detail["voter_name"] for detail in vote_details if detail["target"] is None]
        
        if votes:
            # 找出票数最多的玩家
            max_votes = max(votes.values())
            most_voted = [pid for pid, count in votes.items() if count == max_votes]
            
            print("\n=== 投票结果统计 ===")
            print("\n得票情况：")
            for pid, count in votes.items():
                print(f"- {self.players[pid].name}: {count} 票")
                # 显示投给该玩家的人
                voters = [detail["voter_name"] for detail in vote_details if detail["target"] == pid]
                print(f"  投票者: {', '.join(voters)}")
            
            # 显示弃票玩家
            if abstained_players:
                print(f"\n弃票玩家: {', '.join(abstained_players)}")
            
            # 准备投票结果数据
            vote_results = {
                "vote_counts": votes,
                "vote_details": vote_details,
                "player_names": {pid: self.players[pid].name for pid in self.players},
                "max_votes": max_votes,
                "is_tie": len(most_voted) > 1,
                "abstained_players": abstained_players
            }
            
            if len(most_voted) > 1:
                print("\n【警告】出现平票！")
                print(f"平票玩家：{', '.join([self.players[pid].name for pid in most_voted])}")
                print(f"每人得到 {max_votes} 票")
                
                # 进入补充发言阶段
                vote_results.update({
                    "tied_players": [self.players[pid].name for pid in most_voted],
                    "is_tie": True
                })
                
                # 记录本轮投票结果（平票）
                if hasattr(self.logger, 'log_round_vote'):
                    self.logger.log_round_vote(self.current_round, vote_results)
                
                # 补充发言阶段
                self.tiebreaker_speech_phase(most_voted)
                
                # 重新投票
                print("\n=== 开始重新投票 ===")
                self.voting_phase(revote=True, tied_players=most_voted)
                return  # 重新投票后直接返回，不再执行后续逻辑
            else:
                voted_out = most_voted[0]
                print(f"\n投票最高的是 {self.players[voted_out].name}，得到 {max_votes} 票")
                vote_results.update({
                    "voted_out": voted_out,
                    "voted_out_name": self.players[voted_out].name
                })
            
            # 记录本轮投票结果
            if hasattr(self.logger, 'log_round_vote'):
                self.logger.log_round_vote(self.current_round, vote_results)
            
            print(f"\n{self.players[voted_out].name} 被投票出局")
            
            # 记录投票结果
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "vote",
                "votes": vote_details,
                "result": voted_out,
                "is_tie": len(most_voted) > 1,
                "vote_counts": {pid: count for pid, count in votes.items()},
                "voting_process": {
                    "total_attempts": sum(detail["attempts"] for detail in vote_details),
                    "invalid_votes": len([d for d in vote_details if d["attempts"] > 1]),
                    "discussions": current_round_discussions
                },
                "vote_stats": self.game_state["vote_stats"]  # 添加投票统计到历史记录
            })
            
            # 处理出局，允许发表遗言
            self.kill_player(voted_out, "公投出局", allow_last_words=True)

    def kill_player(self, player_id: str, reason: str, allow_last_words: bool = True) -> None:
        """处理玩家死亡
        
        Args:
            player_id: 死亡玩家ID
            reason: 死亡原因
            allow_last_words: 是否允许发表遗言
        """
        if player_id in self.players:
            player = self.players[player_id]
            if not player.is_alive:
                return
            player.is_alive = False
            self.game_state["players"][player_id]["is_alive"] = False
            
            # 记录生存率
            self._log_survival(player_id)
            
            if player.is_wolf():
                self.game_state["alive_count"]["werewolf"] -= 1
            else:
                self.game_state["alive_count"]["villager"] -= 1
            
            print(f"\n{player.name} 被{reason}")
            
            # 处理警徽转移
            if player_id == self.game_state.get("sheriff"):
                self._handle_sheriff_death(player_id)
            
            # 处理遗言
            if allow_last_words:
                # 第一天晚上死亡或白天死亡的玩家可以发表遗言
                if self.current_round == 1 or reason == "公投出局":
                    print(f"\n{player.name} 的遗言：")
                    agent = self.ai_agents[player_id]
                    last_words = agent.last_words(self.game_state)
                    
                    
                    # 记录遗言
                    self.game_state["history"].append({
                        "round": self.current_round,
                        "phase": self.game_state["phase"],
                        "event": "last_words",
                        "player": player_id,
                        "content": last_words
                    })
            
            # 记录死亡信息
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": self.game_state["phase"],
                "event": "death",
                "player": player_id,
                "reason": reason
            })

            if isinstance(player, WolfBeauty):
                self._handle_wolf_beauty_martyrdom(player)
            
            time.sleep(self.delay)

    def _handle_sheriff_death(self, sheriff_id: str) -> None:
        """处理警长死亡时的警徽转移
        
        Args:
            sheriff_id: 死亡警长ID
        """
        print(f"\n🎖️ 警长 {self.players[sheriff_id].name} 死亡，处理警徽转移...")
        
        # 检查是否有预设的警徽流
        sheriff_flow = self.game_state.get("sheriff_flow")
        
        if sheriff_flow and sheriff_flow in self.players and self.players[sheriff_flow].is_alive:
            # 按照警徽流转移
            self._transfer_sheriff_badge(sheriff_id, sheriff_flow)
        else:
            # 没有预设或预设目标已死亡，警徽作废
            print("【系统】警徽流目标无效，警徽被销毁！")
            self.players[sheriff_id].is_sheriff = False
            self.game_state["sheriff"] = None
            self.game_state["sheriff_badge"] = False
            
            # 记录警徽销毁
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "sheriff_destroy",
                "sheriff": sheriff_id
            })

    def tiebreaker_speech_phase(self, tied_players: List[str]) -> None:
        """平票后的补充发言阶段
        
        Args:
            tied_players: 平票的玩家ID列表
        """
        print("\n=== 平票补充发言阶段 ===")
        print(f"平票玩家：{', '.join([self.players[pid].name for pid in tied_players])}")
        print("请平票玩家依次进行补充发言...")
        time.sleep(self.delay)
        
        # 获取完整对话记录
        full_history = []
        for event in self.game_state["history"]:
            if event.get("phase") in ["discussion", "tiebreaker_speech"]:
                full_history.append({
                    "speaker": self.players[event["player"]].name,
                    "content": event["content"]
                })
        
        history_text = "\n".join([f"{h['speaker']}: {h['content']}" for h in full_history])
        
        # 平票玩家依次发言
        for player_id in tied_players:
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # 检查玩家是否存活
            if not role.is_alive:
                continue
            
            
            # 生成补充发言的提示词
            prompt = f"""
你是{role.name}，在投票中与{'、'.join([self.players[pid].name for pid in tied_players if pid != player_id])}平票。
现在需要你进行补充发言，为自己辩护或说服其他玩家投票给其他人。

当前游戏状态：
- 回合: {self.current_round}
- 存活玩家: {[f"{info['name']}({pid})" for pid, info in self.game_state['players'].items() if info['is_alive']]}

完整对话记录：
{history_text if history_text else '无'}

请给出有力的补充发言，要求：
1. 分析当前局势，解释为什么你不应该被投出局
2. 指出其他平票玩家或存活玩家的可疑之处
3. 使用逻辑和证据支持你的观点
4. 发言要生动形象，加入动作和表情描写（用【】包裹）
5. 至少100字

注意：你现在的处境很危险，需要说服其他玩家不要投给你！
"""
            
            result = agent.discuss(self.game_state, speaker_name=role.name)
            
            # 处理不同类型的返回结果
            if isinstance(result, dict):
                speech = result.get("content", "")
            else:
                speech = result
            
            
            
            # 记录补充发言
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "tiebreaker_speech",
                "player": player_id,
                "content": speech
            })
            
            time.sleep(self.delay)

    def sheriff_election_phase(self) -> bool:
        """警长竞选阶段
        
        Returns:
            bool: 是否成功选出警长
        """
        print("\n=== 警长竞选阶段 ===")
        print("请想要竞选警长的玩家举手...")
        time.sleep(self.delay)
        
        # 获取所有存活玩家
        alive_players = [pid for pid, role in self.players.items() if role.is_alive]
        
        # 收集竞选者
        candidates = []
        for player_id in alive_players:
            agent = self.ai_agents[player_id]
            role = self.players[player_id]
            
            # 询问是否竞选警长
            will_run = self._ask_sheriff_campaign(agent, role, alive_players)
            
            if will_run:
                candidates.append(player_id)
                print(f"{role.name} 参与警长竞选")
            
            time.sleep(self.delay)
        
        if not candidates:
            print("\n没有玩家参与警长竞选，警徽作废")
            self.game_state["sheriff_badge"] = False
            return False
        
        print(f"\n共有 {len(candidates)} 名玩家参与竞选：")
        for pid in candidates:
            print(f"- {self.players[pid].name}")
        
        # 竞选发言
        print("\n=== 竞选发言 ===")
        for player_id in candidates:
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # print(f"\n{role.name} 的竞选发言：")
            
            # 生成竞选发言提示词
            prompt = self._generate_campaign_speech_prompt(role, candidates)
            response = agent.ask_ai(prompt, None, self.game_state, speaker_name=role.name)
            
            # print(response)
            
            # 记录发言
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "sheriff_campaign",
                "player": player_id,
                "content": response
            })
            
            time.sleep(self.delay)
        
        # 退水环节（狼人可以选择退水）
        remaining_candidates = self._sheriff_withdraw_phase(candidates)
        
        if not remaining_candidates:
            print("\n所有竞选者都退水，警徽作废")
            self.game_state["sheriff_badge"] = False
            return False
        
        if len(remaining_candidates) == 1:
            # 只有一名候选人，直接当选
            sheriff_id = remaining_candidates[0]
            self._assign_sheriff_badge(sheriff_id)
            return True
        
        # 投票选出警长
        sheriff_id = self._sheriff_vote_phase(remaining_candidates)
        
        if sheriff_id:
            self._assign_sheriff_badge(sheriff_id)
            return True
        else:
            print("\n警长竞选失败，警徽作废")
            self.game_state["sheriff_badge"] = False
            return False

    def _ask_sheriff_campaign(self, agent, role, alive_players) -> bool:
        """询问玩家是否竞选警长"""
        prompt = f"""
你是{role.name}，现在是警长竞选阶段。

当前存活玩家：
{[f"{self.players[pid].name}({pid})" for pid in alive_players]}

你的身份是：{role.role_type.value}

请决定是否参与警长竞选：
1. 分析竞选警长的利弊
2. 考虑自己的身份和处境
3. 如果是神职（预言家等），竞选可以带队
4. 如果是狼人，竞选可以搅乱局势或自爆吞警徽

请回复"竞选"或"不竞选"。
"""
        
        response = agent.ask_ai(prompt, None, self.game_state, speaker_name=role.name, stream=False)
        if re.search(r'\b竞选\b', response) and not re.search(r'不竞选|不要竞选|放弃竞选', response):
            return True
        if "举手" in response:
            return True
        return False

    def _generate_campaign_speech_prompt(self, role, candidates) -> str:
        """生成竞选发言提示词"""
        return f"""
你是{role.name}，正在参与警长竞选。

其他竞选者：
{[self.players[pid].name for pid in candidates if pid != role.player_id]}

请发表竞选发言：
1. 说明为什么你适合当警长
2. 如果是预言家，可以报查验
3. 表达你的游戏思路和计划
4. 争取其他玩家的支持
5. 发言要生动形象，加入动作描写
6. 至少100字
"""

    def _sheriff_withdraw_phase(self, candidates: List[str]) -> List[str]:
        """退水环节
        
        Args:
            candidates: 当前候选人列表
            
        Returns:
            List[str]: 退水后剩余的候选人
        """
        print("\n=== 退水环节 ===")
        print("竞选者可以选择退水（放弃竞选）...")
        time.sleep(self.delay)
        
        remaining = candidates.copy()
        
        for player_id in candidates:
            if player_id not in remaining:
                continue
                
            role = self.players[player_id]
            agent = self.ai_agents[player_id]
            
            # 询问是否退水
            prompt = f"""
你是{role.name}，正在参与警长竞选。

当前剩余竞选者：
{[self.players[pid].name for pid in remaining]}

请决定是否退水（放弃竞选）：
1. 分析当前竞选局势
2. 如果你是狼人，可以考虑自爆吞警徽
3. 如果局势不利，可以选择退水
4. 如果想继续竞选，坚持不退

请回复"退水"或"坚持"。
"""
            
            response = agent.ask_ai(prompt, None, self.game_state, speaker_name=role.name, stream=False)
            
            # 检查是否自爆
            if role.is_wolf() and not isinstance(role, WolfBeauty) and (("自爆" in response or "爆炸" in response) and not ("不自爆" in response or "不爆炸" in response)):
                print(f"\n【爆炸】{role.name} 选择自爆！")
                self._handle_wolf_explode(player_id, first_day=True)
                return []  # 自爆后竞选结束
            
            if "退水" in response or "放弃" in response:
                remaining.remove(player_id)
                print(f"{role.name} 选择退水")
            else:
                print(f"{role.name} 坚持竞选")
            
            time.sleep(self.delay)
        
        return remaining

    def _sheriff_vote_phase(self, candidates: List[str]) -> Optional[str]:
        """警长投票阶段
        
        Args:
            candidates: 候选人列表
            
        Returns:
            Optional[str]: 选出的警长ID，如果没有选出则返回None
        """
        print("\n=== 警长投票 ===")
        print(f"候选人：{', '.join([self.players[pid].name for pid in candidates])}")
        time.sleep(self.delay)
        
        # 获取非候选人的存活玩家进行投票
        voters = [pid for pid, role in self.players.items() 
                 if role.is_alive and pid not in candidates]
        
        votes = {}
        
        for voter_id in voters:
            role = self.players[voter_id]
            agent = self.ai_agents[voter_id]
            
            
            # 生成投票提示词
            prompt = f"""
你是{role.name}，正在为警长竞选投票。

候选人：
{[f"{self.players[pid].name}({pid})" for pid in candidates]}

历史发言：
{self._get_campaign_speeches()}

请选择你支持的候选人：
1. 分析各位候选人的发言
2. 选择你认为最可信的玩家
3. 用"选择[玩家ID]"格式投票
"""
            
            response = agent.ask_ai(prompt, None, self.game_state, speaker_name=role.name)
            target_id = agent._extract_target(response)
            
            if target_id in candidates:
                votes[target_id] = votes.get(target_id, 0) + 1
                print(f"{role.name} 投票给 {self.players[target_id].name}")
            else:
                print(f"{role.name} 的投票无效")
            
            time.sleep(self.delay)
        
        # 统计票数
        if votes:
            max_votes = max(votes.values())
            winners = [pid for pid, count in votes.items() if count == max_votes]
            
            print("\n=== 投票结果 ===")
            for pid, count in votes.items():
                print(f"{self.players[pid].name}: {count} 票")
            
            if len(winners) == 1:
                return winners[0]
            else:
                print(f"\n平票：{', '.join([self.players[pid].name for pid in winners])}")
                # 平票时随机选择
                return random.choice(winners)
        
        return None

    def _get_campaign_speeches(self) -> str:
        """获取竞选发言记录"""
        speeches = []
        for event in self.game_state["history"]:
            if event.get("phase") == "sheriff_campaign":
                player_name = self.players[event["player"]].name
                speeches.append(f"{player_name}: {event['content']}")
        return "\n".join(speeches) if speeches else "无"

    def _assign_sheriff_badge(self, sheriff_id: str) -> None:
        """分配警徽
        
        Args:
            sheriff_id: 警长ID
        """
        self.game_state["sheriff"] = sheriff_id
        sheriff = self.players[sheriff_id]
        
        print(f"\n🎖️ {sheriff.name} 当选警长！")
        
        # 记录警长任命
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": "sheriff_election",
            "event": "sheriff_assigned",
            "sheriff": sheriff_id
        })
        
        # 设置警长标记
        sheriff.is_sheriff = True

    def sheriff_speech_phase(self) -> None:
        """警长发言阶段（第一天竞选后）"""
        sheriff_id = self.game_state.get("sheriff")
        if not sheriff_id:
            return
        
        sheriff = self.players[sheriff_id]
        agent = self.ai_agents[sheriff_id]
        
        print(f"\n=== 警长 {sheriff.name} 发言 ===")
        
        # 警长安排警徽流
        prompt = f"""
你是{sheriff.name}，刚刚当选警长。

作为警长，你需要：
1. 发表当选感言
2. 安排【警徽流】（如果你死亡，警徽传给谁）
3. 给出游戏的思路和方向

请说明你的警徽流计划：
- 如果你死亡，希望把警徽传给谁？
- 为什么要传给这个人？
- 指定警徽流时，必须使用"警徽流传给[player数字]"或"传给[player数字]"这样的格式来明确指出你的投票目标。
- player ID必须是完整的格式，如player1、player2。

发言要清晰有力，展现警长的领导力！
"""
        
        response = agent.ask_ai(prompt, None, self.game_state)
        # print(response)
        
        # 记录警徽流
        self._parse_sheriff_flow(response, sheriff_id)
        
        # 记录发言
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": "sheriff_speech",
            "player": sheriff_id,
            "content": response
        })
        
        time.sleep(self.delay)

    def _parse_sheriff_flow(self, response: str, sheriff_id: str) -> None:
        """解析警徽流
        
        Args:
            response: 警长发言内容
            sheriff_id: 警长ID
        """
        # 尝试从发言中提取警徽流传授目标
        import re
        
        # 匹配警徽流相关表述
        patterns = [
            r'警徽流(?:传给|给)?\s*\[([^\]]+)\]',   # 警徽流[player3] / 警徽流传给[player3] / 警徽流给[player3]
            r'警徽流(?:传给|给)?[：:]\s*(\w+\d*)',   # 警徽流：player3 / 警徽流传给：player3 / 警徽流给：player3
            r'警徽流(?:传给|给)?\s*(\w+\d+)',        # 警徽流player3 / 警徽流传给player3 / 警徽流给player3
            r'(?:传给|给)\[([^\]]+)\]',             # 传给[player3] / 给[player3]
            r'(?:传给|给)[：:]\s*(\w+\d+)',          # 传给：player3 / 给：player3
            r'(?:传给|给)\s*(\w+\d+)',              # 传给player3 / 给player3
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, response)
            if matches:
                target_name = matches[0]
                # 查找对应的玩家ID
                for pid, info in self.game_state["players"].items():
                    if info["name"] == target_name and pid != sheriff_id:
                        self.game_state["sheriff_flow"] = pid
                        print(f"\n【警徽流】{self.players[sheriff_id].name} -> {target_name}")
                        return
        
        # 如果没有明确指定，随机选择一个
        alive_others = [pid for pid, role in self.players.items() 
                       if role.is_alive and pid != sheriff_id]
        if alive_others:
            self.game_state["sheriff_flow"] = random.choice(alive_others)
            print(f"\n【警徽流】{self.players[sheriff_id].name} -> {self.players[self.game_state['sheriff_flow']].name}")

    def wolf_explode_phase(self) -> bool:
        """狼人自爆阶段
        
        Returns:
            bool: 是否有狼人自爆
        """
        # 获取存活的狼人
        wolves = [pid for pid, role in self.players.items() 
                 if role.is_wolf() and role.is_alive and not isinstance(role, WolfBeauty)]
        
        if not wolves:
            return False
        
        print("\n=== 狼人自爆机会 ===")
        print("狼人可以选择自爆，直接进入夜晚...")
        time.sleep(self.delay)
        
        for wolf_id in wolves:
            agent = self.ai_agents[wolf_id]
            wolf = self.players[wolf_id]
            
            # 询问是否自爆
            prompt = f"""
你是{wolf.name}（狼人），现在是白天发言阶段。

当前局势：
- 回合: {self.current_round}
- 存活玩家: {[f"{info['name']}({pid})" for pid, info in self.game_state['players'].items() if info['is_alive']]}
- 警长: {self.players[self.game_state['sheriff']].name if self.game_state.get('sheriff') else '无'}

你可以选择自爆：
1. 自爆后立即进入夜晚
2. 跳过剩余的白天流程（发言、投票）
3. 自爆的狼人可以指刀（指定今晚的击杀目标）
4. 如果警长竞选阶段自爆，可以吞掉警徽

请决定是否自爆（回复"自爆"或"不自爆"）。
"""
            
            response = agent.ask_ai(prompt, None, self.game_state)
            
            if "不自爆" in response or "不爆炸" in response:
                return False
            elif "自爆" in response or "爆炸" in response:
                print(f"\n💥 {wolf.name} 选择自爆！")
                self._handle_wolf_explode(wolf_id)
                return True
        
        print("\n没有狼人自爆，继续白天流程")
        return False

    def _handle_wolf_explode(self, wolf_id: str, first_day: bool = False) -> None:
        """处理狼人自爆
        
        Args:
            wolf_id: 自爆的狼人ID
            first_day: 是否是第一天
        """
        wolf = self.players[wolf_id]
        
        # 标记狼人死亡
        wolf.is_alive = False
        self.game_state["players"][wolf_id]["is_alive"] = False
        self.game_state["alive_count"]["werewolf"] -= 1
        
        # 增加自爆计数
        self.game_state["wolf_explode_count"] = self.game_state.get("wolf_explode_count", 0) + 1
        
        # 记录自爆
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": "day",
            "event": "wolf_explode",
            "player": wolf_id
        })
        
        print(f"{wolf.name} 自爆身亡！")
        
        # 第一天自爆吞警徽
        if first_day:
            self.game_state["wolf_exploded_first_day"] = True
            self.game_state["sheriff_badge"] = False
            print("【系统】第一天狼人自爆，警徽被吞！")
        
        # 双爆吞警徽规则
        if self.game_state.get("wolf_explode_count", 0) >= 2:
            if self.game_state.get("sheriff"):
                print("【系统】双爆吞警徽！警徽被销毁！")
                old_sheriff = self.game_state["sheriff"]
                self.players[old_sheriff].is_sheriff = False
                self.game_state["sheriff"] = None
                self.game_state["sheriff_badge"] = False
        
        # 自爆狼人指刀
        self._wolf_designate_target(wolf_id)
        
        time.sleep(self.delay)

    def _wolf_designate_target(self, wolf_id: str) -> None:
        """自爆狼人指刀
        
        Args:
            wolf_id: 自爆的狼人ID
        """
        wolf = self.players[wolf_id]
        agent = self.ai_agents[wolf_id]
        
        print(f"\n{wolf.name} 进行指刀...")
        
        # 获取存活的好人
        good_players = [pid for pid, role in self.players.items() 
                       if not role.is_wolf() and role.is_alive]
        
        if not good_players:
            return
        
        prompt = f"""
你是{wolf.name}，刚刚自爆。

现在你可以指刀（指定今晚狼人的击杀目标）。

存活的好人玩家：
{[f"{self.players[pid].name}({pid})" for pid in good_players]}

请选择今晚要击杀的目标：
1. 优先选择神职（预言家、女巫等）
2. 选择对狼人威胁最大的玩家
3. 用"选择[玩家ID]"格式指定目标
"""
        
        response = agent.ask_ai(prompt, None, self.game_state)
        target_id = agent._extract_target(response)
        
        if target_id and target_id in good_players:
            # 设置指刀目标
            self.game_state["designated_target"] = target_id
            print(f"{wolf.name} 指刀：{self.players[target_id].name}")
        else:
            # 随机选择
            target_id = random.choice(good_players)
            self.game_state["designated_target"] = target_id
            print(f"{wolf.name} 指刀：{self.players[target_id].name}（随机）")
        
        # 记录指刀
        self.game_state["history"].append({
            "round": self.current_round,
            "phase": "day",
            "event": "wolf_designate",
            "wolf": wolf_id,
            "target": target_id
        })

    def _transfer_sheriff_badge(self, from_id: str, to_id: str) -> None:
        """转移警徽
        
        Args:
            from_id: 原警长ID
            to_id: 新警长ID
        """
        if from_id:
            self.players[from_id].is_sheriff = False
        
        if to_id and to_id in self.players and self.players[to_id].is_alive:
            self.players[to_id].is_sheriff = True
            self.game_state["sheriff"] = to_id
            print(f"\n🎖️ 警徽从 {self.players[from_id].name} 传给 {self.players[to_id].name}")
            
            # 记录警徽转移
            self.game_state["history"].append({
                "round": self.current_round,
                "phase": "sheriff_transfer",
                "from": from_id,
                "to": to_id
            })

    def check_game_over(self) -> bool:
        """检查游戏是否结束"""
        wolf_count = self.game_state["alive_count"]["werewolf"]
        villager_count = self.game_state["alive_count"]["villager"]
        
        if wolf_count == 0:
            return True

        if self.config.get("rules", {}).get("win_condition") == "屠边":
            alive_villagers = [
                role for role in self.players.values()
                if role.is_alive and role.role_type == RoleType.VILLAGER
            ]
            alive_gods = [
                role for role in self.players.values()
                if role.is_alive and role.is_god()
            ]
            return not alive_villagers or not alive_gods

        if wolf_count >= villager_count:
            return True
        return False

    def _calculate_player_score(self, player_id: str, winner: str) -> float:
        """计算玩家得分
        
        Args:
            player_id: 玩家ID
            winner: 获胜阵营
            
        Returns:
            float: 玩家得分
        """
        role = self.players[player_id]
        player_stats = self.game_state["vote_stats"]["player_stats"].get(player_id, {})
        
        score = 0.0
        
        # 基础分：存活到游戏结束
        if role.is_alive:
            score += 10.0
        
        # 胜负分：胜方+20分，败方+0分
        if winner == "好人阵营" and not role.is_wolf():
            score += 20.0
        elif winner == "狼人阵营" and role.is_wolf():
            score += 20.0
        
        # 投票准确率分
        total_votes = player_stats.get("total_votes", 0)
        invalid_votes = player_stats.get("invalid_votes", 0)
        if total_votes > 0:
            valid_rate = (total_votes - invalid_votes) / total_votes
            score += valid_rate * 10.0
        
        # 角色技能使用分
        if role.role_type == RoleType.SEER:
            # 预言家：验人次数
            seer_checks = self.game_state.get("seer_checks", {}).get(player_id, {})
            score += len(seer_checks) * 5.0
        elif role.role_type == RoleType.WITCH:
            # 女巫：药水使用情况
            witch_actions = self.game_state.get("witch_actions", {}).get(player_id, {})
            score += len(witch_actions) * 3.0
        elif role.role_type == RoleType.HUNTER:
            # 猎人：开枪次数
            hunter_shots = self.game_state.get("hunter_shots", {}).get(player_id, 0)
            score += hunter_shots * 5.0
        
        # 狼人特殊分：刀人成功率
        if role.is_wolf():
            wolf_kills = self.game_state.get("wolf_kills", 0)
            score += wolf_kills * 2.0
        
        # 发言活跃度分
        speech_count = 0
        for event in self.game_state["history"]:
            if event.get("player") == player_id and event.get("phase") in ["discussion", "tiebreaker_speech"]:
                speech_count += 1
        score += speech_count * 1.0
        
        return score
    
    def _select_mvp_and_svp(self, winner: str) -> tuple:
        """选择MVP和SVP
        
        Args:
            winner: 获胜阵营
            
        Returns:
            tuple: (mvp_player_id, svp_player_id)
        """
        mvp_player_id = None
        svp_player_id = None
        mvp_score = -1.0
        svp_score = -1.0
        
        for player_id, role in self.players.items():
            score = self._calculate_player_score(player_id, winner)
            is_winner = (winner == "好人阵营" and not role.is_wolf()) or (winner == "狼人阵营" and role.is_wolf())
            
            if is_winner:
                # 胜方玩家，竞争MVP
                if score > mvp_score:
                    mvp_score = score
                    mvp_player_id = player_id
            else:
                # 败方玩家，竞争SVP
                if score > svp_score:
                    svp_score = score
                    svp_player_id = player_id
        
        return mvp_player_id, svp_player_id
    
    def announce_winner(self) -> None:
        """宣布游戏结果"""
        if self.game_state["alive_count"]["werewolf"] == 0:
            winner = "好人阵营"
        else:
            winner = "狼人阵营"
            
        print(f"\n=== {winner}胜利！===")
        
        # 选择MVP和SVP
        mvp_player_id, svp_player_id = self._select_mvp_and_svp(winner)
        
        # 打印MVP和SVP
        if mvp_player_id:
            mvp_player = self.players[mvp_player_id]
            mvp_score = self._calculate_player_score(mvp_player_id, winner)
            print(f"\n🏆 MVP（胜方最佳玩家）：{mvp_player.name} ({mvp_player.role_type.value})")
            print(f"   得分: {mvp_score:.1f}")
        
        if svp_player_id:
            svp_player = self.players[svp_player_id]
            svp_score = self._calculate_player_score(svp_player_id, winner)
            print(f"\n🥈 SVP（败方最佳玩家）：{svp_player.name} ({svp_player.role_type.value})")
            print(f"   得分: {svp_score:.1f}")
        
        # 打印存活玩家
        print("\n存活玩家：")
        for player_id, role in self.players.items():
            if role.is_alive:
                print(f"- {role.name} ({role.role_type.value})")
        
        # 打印投票统计
        print("\n=== 投票统计 ===")
        total_votes = self.game_state["vote_stats"]["total_votes"]
        invalid_votes = self.game_state["vote_stats"]["invalid_votes"]
        if total_votes > 0:
            invalid_rate = (invalid_votes / total_votes) * 100
            print(f"\n总体投票无效率: {invalid_rate:.1f}%")
            print(f"总投票数: {total_votes}")
            print(f"无效投票数: {invalid_votes}")
            
            print("\n各玩家投票统计：")
            for player_id, stats in self.game_state["vote_stats"]["player_stats"].items():
                player_name = self.players[player_id].name
                player_total = stats["total_votes"]
                player_invalid = stats["invalid_votes"]
                if player_total > 0:
                    player_invalid_rate = (player_invalid / player_total) * 100
                    print(f"\n{player_name}:")
                    print(f"- 投票无效率: {player_invalid_rate:.1f}%")
                    print(f"- 总投票数: {player_total}")
                    print(f"- 无效投票数: {player_invalid}")
                    if player_invalid > 0:
                        print("- 无效原因统计:")
                        reason_counts = {}
                        for record in stats["invalid_reasons"]:
                            reason = record["reason"]
                            reason_counts[reason] = reason_counts.get(reason, 0) + 1
                        for reason, count in reason_counts.items():
                            print(f"  * {reason}: {count}次")
        
        # 收集评估指标数据
        metrics = {}
        if hasattr(self, 'logger') and hasattr(self.logger, 'calculate_metrics'):
            metrics = self.logger.calculate_metrics()
        
        # 设置游戏结果数据
        self.game_state["winner"] = winner
        self.game_state["final_result"] = {
            "end_time": datetime.now().isoformat(),
            "winner": winner,
            "mvp": {
                "player_id": mvp_player_id,
                "player_name": self.players[mvp_player_id].name if mvp_player_id else None,
                "role": self.players[mvp_player_id].role_type.value if mvp_player_id else None,
                "score": self._calculate_player_score(mvp_player_id, winner) if mvp_player_id else None
            } if mvp_player_id else None,
            "svp": {
                "player_id": svp_player_id,
                "player_name": self.players[svp_player_id].name if svp_player_id else None,
                "role": self.players[svp_player_id].role_type.value if svp_player_id else None,
                "score": self._calculate_player_score(svp_player_id, winner) if svp_player_id else None
            } if svp_player_id else None,
            "vote_stats": self.game_state["vote_stats"],
            "metrics": metrics,
            "final_state": {
                "players": self.game_state["players"],
                "alive_count": self.game_state["alive_count"],
                "current_round": self.current_round
            }
        }
        
        # 记录游戏结果
        self.game_state["history"].append({
            "round": self.current_round,
            "event": "game_over",
            "winner": winner,
            "vote_stats": self.game_state["vote_stats"]  # 添加投票统计到历史记录
        }) 
        
        # 调用logger记录游戏结束和指标数据
        if hasattr(self, 'logger') and hasattr(self.logger, 'log_game_over'):
            self.logger.log_game_over(winner, self.game_state)
