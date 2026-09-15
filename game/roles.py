"""
游戏角色定义

主要职责：
1. 定义角色类型枚举
2. 定义基础角色类
3. 实现具体角色类（狼人、村民、神职）
"""

from enum import Enum
from typing import Optional, List
import logging

class RoleType(Enum):
    WEREWOLF = "werewolf"
    WOLF_BEAUTY = "wolf_beauty"  # 狼美人
    VILLAGER = "villager"
    SEER = "seer"        # 预言家
    WITCH = "witch"      # 女巫
    HUNTER = "hunter"    # 猎人
    GUARD = "guard"      # 守卫
    IDIOT = "idiot"      # 白痴
    WOLF_KING = "wolf_king"  # 狼王
    KNIGHT = "knight"    # 骑士

class BaseRole:
    def __init__(self, player_id: str, name: str, role_type: RoleType):
        self.player_id = player_id
        self.name = name
        self.role_type = role_type
        self.is_alive = True
        self.is_sheriff = False  # 是否是警长
        self.used_skills = set()  # 记录已使用的技能
        self.logger = logging.getLogger(__name__)

    def is_wolf(self) -> bool:
        """判断是否是狼人"""
        return self.role_type in [RoleType.WEREWOLF, RoleType.WOLF_BEAUTY]

    def is_god(self) -> bool:
        """判断是否是神职"""
        return self.role_type in [RoleType.SEER, RoleType.WITCH, RoleType.HUNTER, RoleType.GUARD, RoleType.IDIOT, RoleType.KNIGHT]

class Werewolf(BaseRole):
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.WEREWOLF)

class WolfBeauty(BaseRole):
    """狼美人：每晚魅惑一名非狼人，死亡时被魅惑者殉情。"""

    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.WOLF_BEAUTY)
        self.charmed_player_id = None

    def charm(self, target_id: str) -> None:
        """记录当晚被魅惑的玩家。"""
        self.charmed_player_id = target_id
        self.logger.info(f"狼美人魅惑了玩家 {target_id}")

class Villager(BaseRole):
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.VILLAGER)

class Seer(BaseRole):
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.SEER)
        self.checked_players = {}  # 改为字典，存储玩家ID和查验结果

    def can_check(self, target_id: str) -> bool:
        """检查是否可以查验目标玩家
        
        Args:
            target_id: 目标玩家ID
            
        Returns:
            bool: 是否可以查验
        """
        if not self.is_alive:
            self.logger.debug(f"预言家已死亡，无法查验")
            return False
        if target_id in self.checked_players:
            self.logger.debug(f"玩家 {target_id} 已被查验过，结果是{'狼人' if self.checked_players[target_id] else '好人'}")
            return True  # 允许重复查验，但会给出警告
        return True

    def check_role(self, target_id: str, is_wolf: bool) -> None:
        """记录查验结果
        
        Args:
            target_id: 目标玩家ID
            is_wolf: 是否是狼人
        """
        self.checked_players[target_id] = is_wolf
        self.logger.info(f"预言家查验了玩家 {target_id}，结果是{'狼人' if is_wolf else '好人'}")

class Witch(BaseRole):
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.WITCH)
        self.has_poison = True    # 毒药
        self.has_medicine = True  # 解药
        self.used_medicine_this_round = False  # 记录本回合是否使用过解药

    def can_save(self, is_first_night: bool = False) -> bool:
        """检查是否可以使用解药
        
        Args:
            is_first_night: 是否是第一个晚上
            
        Returns:
            bool: 是否可以使用解药
        """
        if not self.is_alive:
            self.logger.debug("女巫已死亡，无法使用解药")
            return False
        if not self.has_medicine:
            self.logger.debug("解药已用完")
            return False
        if self.used_medicine_this_round:
            self.logger.debug("本回合已经使用过解药")
            return False
        return True

    def can_poison(self, is_first_night: bool = False) -> bool:
        """检查是否可以使用毒药
        
        Args:
            is_first_night: 是否是第一个晚上
            
        Returns:
            bool: 是否可以使用毒药
        """
        if not self.is_alive:
            self.logger.debug("女巫已死亡，无法使用毒药")
            return False
        if not self.has_poison:
            self.logger.debug("毒药已用完")
            return False
        if is_first_night:
            self.logger.debug("第一个晚上不建议使用毒药")
            return True  # 允许使用，但会给出警告
        return True

    def use_medicine(self) -> None:
        """使用解药"""
        self.has_medicine = False
        self.used_medicine_this_round = True
        self.logger.info("女巫使用了解药")

    def use_poison(self) -> None:
        """使用毒药"""
        self.has_poison = False
        self.logger.info("女巫使用了毒药")

    def reset_round(self) -> None:
        """重置回合状态"""
        self.used_medicine_this_round = False

class Hunter(BaseRole):
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.HUNTER)
        self.can_shoot = True  # 是否可以开枪
        self.death_confirmed = False  # 是否确认死亡（被投票/被毒/被狼人杀）

    def can_use_gun(self) -> bool:
        """检查是否可以开枪
        
        Returns:
            bool: 是否可以开枪
        """
        if not self.death_confirmed:
            self.logger.debug("猎人未确认死亡，不能开枪")
            return False
        if not self.can_shoot:
            self.logger.debug("猎人已经开过枪了")
            return False
        return True

    def confirm_death(self) -> None:
        """确认死亡状态"""
        self.death_confirmed = True
        self.logger.info("猎人死亡已确认")

    def use_gun(self) -> None:
        """使用开枪技能"""
        if self.can_use_gun():
            self.can_shoot = False
            self.logger.info("猎人开枪了")
        else:
            self.logger.warning("猎人无法开枪")

class Guard(BaseRole):
    """守卫角色"""
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.GUARD)
        self.last_guarded = None  # 上一晚守护的玩家ID
        self.can_guard = True  # 是否可以守护

    def can_guard_target(self, target_id: str, same_guard_same_target: bool = False) -> bool:
        """检查是否可以守护目标
        
        Args:
            target_id: 目标玩家ID
            same_guard_same_target: 是否允许连续守护同一目标
            
        Returns:
            bool: 是否可以守护
        """
        if not self.is_alive:
            self.logger.debug("守卫已死亡，无法守护")
            return False
        if not same_guard_same_target and target_id == self.last_guarded:
            self.logger.debug("不能连续两晚守护同一人")
            return False
        return True

    def guard_player(self, target_id: str) -> None:
        """守护目标玩家
        
        Args:
            target_id: 目标玩家ID
        """
        self.last_guarded = target_id
        self.logger.info(f"守卫守护了玩家 {target_id}")

    def reset_round(self) -> None:
        """重置回合状态"""
        pass

class Idiot(BaseRole):
    """白痴角色"""
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.IDIOT)
        self.revealed = False  # 是否已经翻牌
        self.can_vote = True  # 是否可以投票
        self.can_be_voted = True  # 是否可以被投票

    def can_reveal(self) -> bool:
        """检查是否可以翻牌
        
        Returns:
            bool: 是否可以翻牌
        """
        if not self.is_alive:
            self.logger.debug("白痴已死亡，无法翻牌")
            return False
        if self.revealed:
            self.logger.debug("白痴已经翻牌过了")
            return False
        return True

    def reveal(self) -> None:
        """翻牌免死"""
        if self.can_reveal():
            self.revealed = True
            self.can_vote = False
            self.can_be_voted = False
            self.logger.info("白痴翻牌免死，失去投票权和被投票权")
        else:
            self.logger.warning("白痴无法翻牌")

class WolfKing(BaseRole):
    """狼王角色"""
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.WEREWOLF)
        self.can_shoot = True  # 是否可以开枪
        self.death_confirmed = False  # 是否确认死亡
        self.can_explode = True  # 是否可以自爆

    def can_use_gun(self) -> bool:
        """检查是否可以开枪
        
        Returns:
            bool: 是否可以开枪
        """
        if not self.death_confirmed:
            self.logger.debug("狼王未确认死亡，不能开枪")
            return False
        if not self.can_shoot:
            self.logger.debug("狼王已经开过枪了")
            return False
        return True

    def confirm_death(self) -> None:
        """确认死亡状态"""
        self.death_confirmed = True
        self.logger.info("狼王死亡已确认")

    def use_gun(self) -> None:
        """使用开枪技能"""
        if self.can_use_gun():
            self.can_shoot = False
            self.logger.info("狼王开枪了")
        else:
            self.logger.warning("狼王无法开枪")

    def can_explode(self) -> bool:
        """检查是否可以自爆
        
        Returns:
            bool: 是否可以自爆
        """
        if not self.is_alive:
            self.logger.debug("狼王已死亡，无法自爆")
            return False
        if not self.can_explode:
            self.logger.debug("狼王已经自爆过了")
            return False
        return True

    def explode(self) -> None:
        """自爆"""
        if self.can_explode():
            self.can_explode = False
            self.logger.info("狼王自爆了")
        else:
            self.logger.warning("狼王无法自爆")

class Knight(BaseRole):
    """骑士角色"""
    def __init__(self, player_id: str, name: str):
        super().__init__(player_id, name, RoleType.KNIGHT)
        self.can_challenge = True  # 是否可以挑战
        self.challenged = False  # 是否已经挑战过

    def can_challenge_player(self, target_id: str) -> bool:
        """检查是否可以挑战目标
        
        Args:
            target_id: 目标玩家ID
            
        Returns:
            bool: 是否可以挑战
        """
        if not self.is_alive:
            self.logger.debug("骑士已死亡，无法挑战")
            return False
        if not self.can_challenge:
            self.logger.debug("骑士已经挑战过了")
            return False
        if target_id == self.player_id:
            self.logger.debug("骑士不能挑战自己")
            return False
        return True

    def challenge(self, target_id: str) -> None:
        """挑战目标玩家
        
        Args:
            target_id: 目标玩家ID
        """
        if self.can_challenge_player(target_id):
            self.can_challenge = False
            self.challenged = True
            self.logger.info(f"骑士挑战了玩家 {target_id}")
        else:
            self.logger.warning("骑士无法挑战")
