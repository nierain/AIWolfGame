"""
游戏程序入口

主要职责：
1. 程序初始化
   - 解析命令行参数
   - 加载配置文件
   - 初始化日志系统
   - 创建游戏实例

2. 游戏运行控制
   - 启动游戏
   - 异常处理
   - 程序退出处理
   - 断点续玩功能
   - 多轮游戏统计

3. 与其他模块的交互：
   - 创建 GameController 实例
   - 使用 utils 中的工具函数
   - 调用 logger 记录主程序日志

主要流程：
if __name__ == "__main__":
    # 解析命令行参数
    # 加载配置
    # 初始化日志
    # 创建游戏实例
    # 运行游戏
    # 处理退出
""" 

import argparse
import json
import logging
import sys
import os
from pathlib import Path
from game.game_controller import GameController
from utils.logger import setup_logger
from utils.game_utils import load_config, validate_game_config
from typing import List, Dict, Optional
import copy
import csv
from datetime import datetime

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='AI狼人杀模拟器 - 基于大语言模型的多智能体狼人杀游戏',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 自动选择人数局（根据配置的模型数量）
  python main.py --rounds 1 --delay 0.5
  
  # 指定9人局
  python main.py --preset 9 --rounds 1 --delay 0.5
  
  # 调试模式运行
  python main.py --preset 8 --rounds 1 --debug
  
  # 从中断处继续
  python main.py --resume --rounds 100
  
  # 使用自定义配置文件
  python main.py --ai-config config/my_ai.json --role-config config/my_roles.json
        """
    )
    parser.add_argument('--role-config', type=str, default='config/role_config.json', 
                      help='角色配置文件路径 (默认: config/role_config.json)')
    parser.add_argument('--ai-config', type=str, default='config/ai_config.json',
                      help='AI配置文件路径 (默认: config/ai_config.json)')
    parser.add_argument('--debug', action='store_true', 
                      help='启用调试模式，输出更详细的日志信息')
    parser.add_argument('--delay', type=float, default=1.0,
                      help='每个动作之间的延迟时间(秒)，用于控制游戏节奏 (默认: 1.0)')
    parser.add_argument('--rounds', type=int, default=100,
                      help='要运行的游戏轮数 (默认: 100)')
    parser.add_argument('--resume', action='store_true',
                      help='从上次中断处继续游戏，会读取 logs/checkpoint.json')
    parser.add_argument('--export-path', type=str, default='analysis',
                      help='评测数据导出路径 (默认: analysis)')
    parser.add_argument('--preset', type=str, choices=['6', '7', '8', '9', '10', '11', '12'],
                      help='直接选择预设人数局(6-12人)，不指定则根据模型数量自动询问')
    return parser.parse_args()

def load_checkpoint():
    """加载游戏断点数据"""
    checkpoint_file = 'logs/checkpoint.json'
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载断点数据失败: {str(e)}")
    return None

def save_checkpoint(completed_rounds: int, statistics: dict):
    """保存游戏断点数据"""
    checkpoint_file = 'logs/checkpoint.json'
    try:
        checkpoint_data = {
            "completed_rounds": completed_rounds,
            "statistics": statistics
        }
        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存断点数据失败: {str(e)}")

def initialize_statistics():
    """初始化统计数据"""
    return {
        "total_games": 0,
        "werewolf_wins": 0,
        "villager_wins": 0,
        "model_stats": {},  # 每个模型的详细统计
        "role_stats": {     # 每个角色的表现统计
            "werewolf": {"wins": 0, "total": 0},
            "villager": {"wins": 0, "total": 0},
            "seer": {"wins": 0, "total": 0},
            "witch": {"wins": 0, "total": 0},
            "hunter": {"wins": 0, "total": 0},
            "guard": {"wins": 0, "total": 0},
            "knight": {"wins": 0, "total": 0},
            "wolf_beauty": {"wins": 0, "total": 0}
        },
        "metrics": {        # 评估指标统计
            "role_recognition_accuracy": [],
            "deception_success_rate": [],
            "voting_accuracy": [],
            "communication_effectiveness": [],
            "survival_rate": [],
            "ability_usage_accuracy": []
        },
        "role_assignments": [],  # 记录每轮的角色分配
        "model_performance": {},  # 每个模型在不同角色下的表现
        "game_details": []  # 每局游戏的详细信息
    }

def assign_models_to_roles(models: List[str], roles: Dict, round_num: int, interval: int) -> Dict:
    """根据轮次分配模型到角色
    
    Args:
        models: 待评估的模型列表
        roles: 角色配置
        round_num: 当前轮次
        interval: 角色轮换间隔
        
    Returns:
        Dict: 角色到模型的映射
    """
    # 计算当前轮次的轮换次数
    rotation = (round_num // interval) % len(models)
    
    # 获取所有角色ID
    all_roles = []
    for role_type, role_dict in roles.items():
        for role_id in role_dict.keys():
            all_roles.append(role_id)
    
    # 根据轮换次数调整模型顺序
    rotated_models = models[rotation:] + models[:rotation]
    
    # 分配模型到角色
    assignments = {}
    for i, role_id in enumerate(all_roles):
        model_index = i % len(rotated_models)
        assignments[role_id] = rotated_models[model_index]
    
    return assignments

def get_model_assignments_from_config(config: Dict, round_num: int) -> Dict:
    """从配置文件中获取指定轮次的角色分配
    
    Args:
        config: 游戏配置
        round_num: 当前轮次（从0开始）
        
    Returns:
        Dict: 角色到模型的映射
    """
    # 检查配置文件中是否有多轮分配配置
    if "multi_round_assignments" not in config:
        # 如果没有多轮分配配置，使用原来的随机分配逻辑
        return assign_models_to_roles(
            config.get("models_to_evaluate", []),
            config["roles"],
            round_num,
            config["game_settings"]["role_rotation_interval"]
        )
    
    # 获取多轮分配配置
    assignments_config = config["multi_round_assignments"]
    
    # 计算实际轮次（从1开始）
    actual_round = round_num + 1
    
    # 查找对应轮次的分配配置
    for round_config in assignments_config:
        if round_config["round"] == actual_round:
            return round_config["assignments"]
    
    # 如果找不到对应轮次的配置，使用最后一个配置的轮次模数
    if assignments_config:
        max_configured_round = max(cfg["round"] for cfg in assignments_config)
        for round_config in assignments_config:
            if round_config["round"] == (actual_round - 1) % max_configured_round + 1:
                return round_config["assignments"]
    
    # 如果仍然找不到，使用原来的随机分配逻辑
    return assign_models_to_roles(
        config.get("models_to_evaluate", []),
        config["roles"],
        round_num,
        config["game_settings"]["role_rotation_interval"]
    )

def export_analysis(statistics: dict, config: dict, export_path: str):
    """导出分析数据
    
    Args:
        statistics: 统计数据
        config: AI配置
        export_path: 导出路径
    """
    os.makedirs(export_path, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 导出JSON格式
    if "json" in config["evaluation_settings"]["export_format"]:
        json_path = os.path.join(export_path, f'analysis_{timestamp}.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(statistics, f, ensure_ascii=False, indent=2)
    
    # 导出CSV格式
    if "csv" in config["evaluation_settings"]["export_format"]:
        # 模型总体表现
        model_performance_path = os.path.join(export_path, f'model_performance_{timestamp}.csv')
        with open(model_performance_path, 'w', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Model', 'Games', 'Wins', 'Win Rate'] + list(statistics["metrics"].keys()))
            for model, stats in statistics["model_stats"].items():
                metrics = [sum(stats["metrics"][m])/len(stats["metrics"][m]) if stats["metrics"][m] else 0 
                          for m in statistics["metrics"].keys()]
                writer.writerow([
                    model,
                    stats["games"],
                    stats["wins"],
                    stats["wins"]/stats["games"] if stats["games"] > 0 else 0
                ] + metrics)
        
        # 角色表现
        role_performance_path = os.path.join(export_path, f'role_performance_{timestamp}.csv')
        with open(role_performance_path, 'w', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Role', 'Games', 'Wins', 'Win Rate'])
            for role, stats in statistics["role_stats"].items():
                if stats["total"] > 0:
                    writer.writerow([
                        role,
                        stats["total"],
                        stats["wins"],
                        stats["wins"]/stats["total"]
                    ])
        
        # 详细游戏记录
        game_details_path = os.path.join(export_path, f'game_details_{timestamp}.csv')
        with open(game_details_path, 'w', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Game ID', 'Round', 'Winner', 'Duration',
                'Wolf Models', 'Village Models', 'Special Role Models',
                'Key Events'
            ])
            for game in statistics["game_details"]:
                writer.writerow([
                    game["game_id"],
                    game["round"],
                    game["winner"],
                    game["duration"],
                    "|".join(game["wolf_models"]),
                    "|".join(game["village_models"]),
                    "|".join(game["special_role_models"]),
                    "|".join(game["key_events"])
                ])

def update_statistics(statistics: dict, game_result: dict, model_assignments: dict):
    """更新统计数据"""
    statistics["total_games"] += 1
    game_id = f"game_{statistics['total_games']}"
    
    # 确保winner字段存在并处理
    winner = game_result.get("winner", "未知")
    if winner == "好人阵营":
        statistics["villager_wins"] += 1
    elif winner == "狼人阵营":
        statistics["werewolf_wins"] += 1
    else:
        # 如果winner字段不存在或为其他值，尝试从final_result中获取
        if "final_result" in game_result and "winner" in game_result["final_result"]:
            winner = game_result["final_result"]["winner"]
            if winner == "好人阵营":
                statistics["villager_wins"] += 1
            elif winner == "狼人阵营":
                statistics["werewolf_wins"] += 1
    
    # 记录本局详细信息
    game_detail = {
        "game_id": game_id,
        "round": game_result.get("current_round", 0),
        "winner": winner,
        "duration": 0,  # 默认值
        "wolf_models": [],
        "village_models": [],
        "special_role_models": [],
        "key_events": [],
        "model_assignments": model_assignments  # 记录本轮的角色分配
    }
    
    # 计算游戏时长
    if "start_time" in game_result and "final_result" in game_result and "end_time" in game_result["final_result"]:
        try:
            start_time = datetime.fromisoformat(game_result["start_time"])
            end_time = datetime.fromisoformat(game_result["final_result"]["end_time"])
            game_detail["duration"] = (end_time - start_time).total_seconds()
        except Exception as e:
            logging.error(f"计算游戏时长出错: {str(e)}")
    
    # 提取指标数据，从final_result中的metrics字段获取
    metrics_data = {}
    if "final_result" in game_result and "metrics" in game_result["final_result"]:
        metrics_data = game_result["final_result"]["metrics"]
    
    final_state = game_result.get("final_state") or game_result.get("final_result", {}).get("final_state", {})
    wolf_roles = {"werewolf", "wolf_king", "stone_ghost", "white_wolf_king", "blood_moon_disciple", "wolf_beauty"}

    # 更新模型统计
    if "players" in final_state:
        for player_id, player_data in final_state["players"].items():
            model_type = model_assignments.get(player_id, "unknown")
            role = player_data.get("role", "unknown")
            
            # 更新模型在不同角色下的表现
            if model_type not in statistics["model_performance"]:
                statistics["model_performance"][model_type] = {
                    role_type: {"games": 0, "wins": 0} 
                    for role_type in ["werewolf", "wolf_beauty", "villager", "seer", "witch", "hunter", "guard", "knight"]
                }
            
            if role in statistics["model_performance"][model_type]:
                statistics["model_performance"][model_type][role]["games"] += 1
                if (winner == "狼人阵营" and role in wolf_roles) or \
                   (winner == "好人阵营" and role not in wolf_roles):
                    statistics["model_performance"][model_type][role]["wins"] += 1
            
            # 更新model_stats数据结构
            if model_type not in statistics["model_stats"]:
                statistics["model_stats"][model_type] = {
                    "games": 0,
                    "wins": 0,
                    "metrics": {metric: [] for metric in statistics["metrics"]}
                }
            
            statistics["model_stats"][model_type]["games"] += 1
            if (winner == "狼人阵营" and role in wolf_roles) or \
               (winner == "好人阵营" and role not in wolf_roles):
                statistics["model_stats"][model_type]["wins"] += 1
                
            # 如果存在指标数据，更新到对应模型的指标中
            for metric_name, value in metrics_data.items():
                if metric_name in statistics["metrics"]:
                    if model_type in statistics["model_stats"]:
                        if metric_name in statistics["model_stats"][model_type]["metrics"]:
                            statistics["model_stats"][model_type]["metrics"][metric_name].append(value)
            
            # 更新游戏详情
            if role in wolf_roles:
                game_detail["wolf_models"].append(model_type)
            elif role in ["seer", "witch", "hunter"]:
                game_detail["special_role_models"].append(f"{role}:{model_type}")
            else:
                game_detail["village_models"].append(model_type)
    
    # 记录关键事件
    if "history" in game_result:
        for event in game_result["history"]:
            if isinstance(event, dict) and "event" in event and event["event"] in ["death", "wolf_identify", "seer_check", "witch_action"]:
                game_detail["key_events"].append(f"{event.get('round', 0)}_{event['event']}")
    
    statistics["game_details"].append(game_detail)
    
    # 更新角色统计
    if "players" in final_state:
        for player_id, player_data in final_state["players"].items():
            role = player_data.get("role", "unknown")
            if role in statistics["role_stats"]:
                statistics["role_stats"][role]["total"] += 1
                if (winner == "狼人阵营" and role in wolf_roles) or \
                   (winner == "好人阵营" and role not in wolf_roles):
                    statistics["role_stats"][role]["wins"] += 1
    
    # 更新评估指标
    for metric_name, value in metrics_data.items():
        if metric_name in statistics["metrics"]:
            statistics["metrics"][metric_name].append(value)
    
    # 记录角色分配情况
    statistics["role_assignments"].append({
        "game_id": game_id,
        "round_num": statistics["total_games"],
        "assignments": model_assignments
    })

def print_statistics(statistics: dict):
    """打印统计结果"""
    print("\n" + "="*60)
    print("游戏统计总结")
    print("="*60)
    print(f"总场次: {statistics['total_games']}")
    
    # 添加除零保护
    if statistics['total_games'] > 0:
        print(f"狼人胜率: {statistics['werewolf_wins']/statistics['total_games']:.2%}")
        print(f"好人胜率: {statistics['villager_wins']/statistics['total_games']:.2%}")
    else:
        print("狼人胜率: 0.00%")
        print("好人胜率: 0.00%")
    
    print("\n各角色胜率:")
    has_role_stats = False
    for role, stats in statistics["role_stats"].items():
        if stats["total"] > 0:
            has_role_stats = True
            win_rate = stats["wins"] / stats["total"]
            print(f"  {role}: {win_rate:.2%} ({stats['wins']}/{stats['total']})")
    if not has_role_stats:
        print("  暂无角色胜率数据")
    
    print("\n各模型表现:")
    has_model_stats = False
    for model, stats in statistics["model_stats"].items():
        if stats["games"] > 0:
            has_model_stats = True
            win_rate = stats["wins"] / stats["games"]
            print(f"  {model}: 胜率 {win_rate:.2%} ({stats['wins']}/{stats['games']})")
    if not has_model_stats:
        print("  暂无模型表现数据")
    
    print("\n评估指标平均值:")
    for metric_name, values in statistics["metrics"].items():
        if values:
            avg_value = sum(values) / len(values)
            print(f"  {metric_name}: {avg_value:.2%}")
    print("="*60)

def load_preset_config(preset_num: str) -> Dict:
    """加载预设配置
    
    Args:
        preset_num: 预设人数(6-12)
        
    Returns:
        Dict: 预设配置
    """
    preset_file = 'config/preset_configs.json'
    try:
        with open(preset_file, 'r', encoding='utf-8') as f:
            preset_configs = json.load(f)
        
        if preset_num not in preset_configs:
            print(f"错误：没有找到{preset_num}人局的预设配置")
            return None
        
        preset_data = preset_configs[preset_num]
        configurations = preset_data["configurations"]
        
        print(f"\n{preset_data['name']}")
        print(f"{preset_data['description']}")
        print("\n可选配置：")
        for i, config in enumerate(configurations, 1):
            print(f"{i}. {config['name']}")
        
        while True:
            choice = input(f"\n请选择配置(1-{len(configurations)})，或输入 'c' 取消: ").strip()
            if choice.lower() == 'c':
                return None
            
            try:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(configurations):
                    selected_config = configurations[choice_idx]
                    return {
                        "preset_name": preset_data['name'],
                        "config_name": selected_config['name'],
                        "total_players": int(preset_num),
                        "werewolf": selected_config.get("werewolf", 0),
                        "wolf_beauty": selected_config.get("wolf_beauty", 0),
                        "wolf_king": selected_config.get("wolf_king", 0),
                        "stone_ghost": selected_config.get("stone_ghost", 0),
                        "white_wolf_king": selected_config.get("white_wolf_king", 0),
                        "blood_moon_disciple": selected_config.get("blood_moon_disciple", 0),
                        "seer": selected_config.get("seer", 0),
                        "witch": selected_config.get("witch", 0),
                        "hunter": selected_config.get("hunter", 0),
                        "guard": selected_config.get("guard", 0),
                        "knight": selected_config.get("knight", 0),
                        "villager": selected_config.get("villager", 0),
                        "custom_roles": selected_config.get("custom_roles", {}),
                        "rules": selected_config.get("rules", {})
                    }
                else:
                    print(f"请输入1-{len(configurations)}之间的数字")
            except ValueError:
                print("请输入有效的数字")
    
    except Exception as e:
        logging.error(f"加载预设配置失败: {str(e)}")
        return None

def select_preset_by_model_count(models: List[str]) -> Optional[Dict]:
    """根据模型数量选择预设配置
    
    Args:
        models: 可用模型列表
        
    Returns:
        Dict: 选中的预设配置，如果用户取消则返回None
    """
    preset_file = 'config/preset_configs.json'
    try:
        with open(preset_file, 'r', encoding='utf-8') as f:
            preset_configs = json.load(f)
        
        model_count = len(models)
        print(f"\n当前配置了 {model_count} 个模型")
        
        # 筛选可用的预设（人数 <= 模型数量）
        available_presets = []
        for preset_num in ['6', '7', '8', '9', '10', '11', '12']:
            if preset_num in preset_configs and int(preset_num) <= model_count:
                available_presets.append((preset_num, preset_configs[preset_num]))
        
        if not available_presets:
            print(f"错误：模型数量({model_count})不足以支持最低6人局")
            print("请至少配置6个模型才能运行游戏")
            return None
        
        print(f"\n可选的预设局（人数 <= {model_count}）：")
        for i, (preset_num, preset_data) in enumerate(available_presets, 1):
            print(f"{i}. {preset_num}人局 - {preset_data['name']}")
        
        while True:
            choice = input(f"\n请选择预设局(1-{len(available_presets)})，或输入 'c' 取消: ").strip()
            if choice.lower() == 'c':
                return None
            
            try:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(available_presets):
                    preset_num, preset_data = available_presets[choice_idx]
                    
                    # 显示该人数下的配置选项
                    configurations = preset_data["configurations"]
                    print(f"\n{preset_data['name']}")
                    print(f"{preset_data['description']}")
                    print("\n可选配置：")
                    for i, config in enumerate(configurations, 1):
                        print(f"{i}. {config['name']}")
                    
                    while True:
                        config_choice = input(f"\n请选择配置(1-{len(configurations)})，或输入 'b' 返回: ").strip()
                        if config_choice.lower() == 'b':
                            break
                        
                        try:
                            config_idx = int(config_choice) - 1
                            if 0 <= config_idx < len(configurations):
                                selected_config = configurations[config_idx]
                                return {
                                    "preset_name": preset_data['name'],
                                    "config_name": selected_config['name'],
                                    "total_players": int(preset_num),
                                    "werewolf": selected_config.get("werewolf", 0),
                                    "wolf_beauty": selected_config.get("wolf_beauty", 0),
                                    "wolf_king": selected_config.get("wolf_king", 0),
                                    "stone_ghost": selected_config.get("stone_ghost", 0),
                                    "white_wolf_king": selected_config.get("white_wolf_king", 0),
                                    "blood_moon_disciple": selected_config.get("blood_moon_disciple", 0),
                                    "seer": selected_config.get("seer", 0),
                                    "witch": selected_config.get("witch", 0),
                                    "hunter": selected_config.get("hunter", 0),
                                    "guard": selected_config.get("guard", 0),
                                    "knight": selected_config.get("knight", 0),
                                    "villager": selected_config.get("villager", 0),
                                    "custom_roles": selected_config.get("custom_roles", {}),
                                    "rules": selected_config.get("rules", {})
                                }
                            else:
                                print(f"请输入1-{len(configurations)}之间的数字")
                        except ValueError:
                            print("请输入有效的数字")
                else:
                    print(f"请输入1-{len(available_presets)}之间的数字")
            except ValueError:
                print("请输入有效的数字")
    
    except Exception as e:
        logging.error(f"加载预设配置失败: {str(e)}")
        return None

def apply_preset_config(preset_config: Dict, role_config: Dict) -> Dict:
    """应用预设配置到角色配置
    
    Args:
        preset_config: 预设配置
        role_config: 原始角色配置
        
    Returns:
        Dict: 更新后的角色配置
    """
    updated_config = copy.deepcopy(role_config)
    
    # 更新总人数
    updated_config["game_settings"]["total_players"] = preset_config["total_players"]
    
    # 更新角色数量（包括自定义角色）
    role_counts = {
        "werewolf": preset_config["werewolf"],
        "seer": preset_config["seer"],
        "witch": preset_config["witch"],
        "hunter": preset_config["hunter"],
        "guard": preset_config.get("guard", 0),
        "knight": preset_config.get("knight", 0),
        "villager": preset_config["villager"]
    }
    
    # 添加自定义角色
    custom_roles = preset_config.get("custom_roles", {})
    for role_name, count in custom_roles.items():
        role_counts[role_name] = count
    
    # 处理特殊狼人角色（狼王、石像鬼、白狼王、血月使徒）
    # 这些角色虽然单独列出，但应该计入 werewolf 总数
    special_wolf_roles = {
        "wolf_king": preset_config.get("wolf_king", 0),
        "stone_ghost": preset_config.get("stone_ghost", 0),
        "white_wolf_king": preset_config.get("white_wolf_king", 0),
        "blood_moon_disciple": preset_config.get("blood_moon_disciple", 0),
        "wolf_beauty": preset_config.get("wolf_beauty", 0)
    }
    
    # 将特殊狼人角色添加到 role_counts
    for role_name, count in special_wolf_roles.items():
        if count > 0:
            role_counts[role_name] = count
    
    updated_config["role_counts"] = role_counts
    
    # 保存特殊狼人角色信息，用于后续处理
    updated_config["special_wolf_roles"] = special_wolf_roles
    updated_config["rules"] = preset_config.get("rules", {})
    
    # 更新玩家列表
    total_players = preset_config["total_players"]
    existing_players = role_config.get("players", {})
    
    # 如果需要更多玩家，添加新玩家
    if len(existing_players) < total_players:
        player_names = ["小欧", "小克", "小深", "小杰", "小千", "小格", "小拉", "小奇", 
                       "小文", "小博", "小灵", "小默"]
        personalities = ["狡猾", "激进", "冷静", "谨慎", "果断", "积极", "多疑", "沉稳",
                        "机智", "勇敢", "理性", "敏锐"]
        
        for i in range(len(existing_players), total_players):
            player_id = f"player{i + 1}"
            name = player_names[i % len(player_names)]
            personality = personalities[i % len(personalities)]
            updated_config["players"][player_id] = {
                "name": name,
                "personality": personality
            }
    # 如果玩家太多，删除多余的
    elif len(existing_players) > total_players:
        players_to_remove = list(existing_players.keys())[total_players:]
        for player_id in players_to_remove:
            del updated_config["players"][player_id]
    
    # 更新多轮分配配置
    if "multi_round_assignments" in updated_config:
        # 获取可用的模型列表
        available_models = []
        for round_config in updated_config["multi_round_assignments"]:
            for player_id, model in round_config["assignments"].items():
                if model not in available_models:
                    available_models.append(model)
        
        # 更新每轮的分配，确保包含所有当前玩家
        for round_config in updated_config["multi_round_assignments"]:
            assignments = {}
            player_ids = list(updated_config["players"].keys())
            
            # 重新分配模型给所有玩家
            for i, player_id in enumerate(player_ids):
                # 循环使用模型
                model = available_models[i % len(available_models)]
                assignments[player_id] = model
            
            round_config["assignments"] = assignments
    
    return updated_config

def main():
    """主函数"""
    # 解析命令行参数
    args = parse_args()
    
    # 初始化日志系统
    setup_logger(debug=args.debug)
    logger = logging.getLogger(__name__)
    
    try:
        # 加载配置文件
        logger.info("正在加载配置文件...")
        role_config = load_config(args.role_config)
        ai_config = load_config(args.ai_config)
        
        # 获取要评估的模型列表
        models_to_evaluate = ai_config["evaluation_settings"]["models_to_evaluate"]
        
        # 处理预设配置
        if args.preset:
            preset_config = load_preset_config(args.preset)
            if preset_config is None:
                logger.info("用户取消了预设配置选择")
                return 0
            
            role_config = apply_preset_config(preset_config, role_config)
            logger.info(f"已应用预设配置: {preset_config['config_name']}")
        else:
            # 如果没有指定预设，根据模型数量询问用户选择
            preset_config = select_preset_by_model_count(models_to_evaluate)
            if preset_config is None:
                logger.info("用户取消了预设配置选择")
                return 0
            
            role_config = apply_preset_config(preset_config, role_config)
            logger.info(f"已应用预设配置: {preset_config['config_name']}")
        
        # 验证人数是否满足最低要求
        total_players = role_config["game_settings"]["total_players"]
        if total_players < 6:
            print(f"\n错误：游戏人数不能少于6人（当前：{total_players}人）")
            print("请使用 --preset 参数选择6-12人局的预设配置")
            return 1
        
        # 验证配置
        if not validate_game_config(role_config):
            logger.error("角色配置文件验证失败")
            return 1
        
        role_rotation_interval = role_config["game_settings"]["role_rotation_interval"]
        
        # 初始化或加载统计数据
        statistics = initialize_statistics()
        start_round = 0
        
        if args.resume:
            checkpoint = load_checkpoint()
            if checkpoint:
                start_round = checkpoint["completed_rounds"]
                statistics = checkpoint["statistics"]
                logger.info(f"从第 {start_round + 1} 轮继续游戏")
            else:
                logger.warning("未找到断点数据，从头开始游戏")
        
        use_random_roles = role_config.get("game_settings", {}).get("random_roles", False)
        
        # 运行多轮游戏
        for round_num in range(start_round, args.rounds):
            try:
                model_assignments = get_model_assignments_from_config(role_config, round_num)
                
                if use_random_roles:
                    game_config = {
                        "game_settings": role_config["game_settings"],
                        "role_counts": role_config.get("role_counts", {}),
                        "players": role_config.get("players", {}),
                        "model_assignments": model_assignments,
                        "ai_players": ai_config["ai_players"],
                        "rules": role_config.get("rules", {}),
                        "delay": args.delay,
                        "total_rounds": args.rounds
                    }
                else:
                    game_roles = copy.deepcopy(role_config["roles"])
                    for role_type, roles in game_roles.items():
                        for role_id in roles:
                            roles[role_id]["ai_type"] = model_assignments[role_id]
                    
                    game_config = {
                        "roles": game_roles,
                        "game_settings": role_config["game_settings"],
                        "ai_players": ai_config["ai_players"],
                        "rules": role_config.get("rules", {}),
                        "delay": args.delay,
                        "total_rounds": args.rounds
                    }
                
                logger.info(f"正在初始化第 {round_num + 1} 轮游戏...")
                game = GameController(game_config)
                
                print(f"\n{'='*60}")
                print(f"第 {round_num + 1}/{args.rounds} 轮游戏开始")
                print('='*60)
                print("本轮模型分配:")
                for role_id, model in model_assignments.items():
                    print(f"  {role_id}: {model}")
                print()
                
                logger.info("游戏开始...")
                game.run_game()
                
                game_result = game.game_state
                update_statistics(statistics, game_result, model_assignments)
                
                save_checkpoint(round_num + 1, statistics)
                export_analysis(statistics, ai_config, args.export_path)
                
                print(f"\n第 {round_num + 1} 轮游戏结束")
                logger.info("游戏结束")
                
            except KeyboardInterrupt:
                print("\n游戏被用户中断")
                logger.info(f"游戏在第 {round_num + 1} 轮被用户中断")
                save_checkpoint(round_num, statistics)
                export_analysis(statistics, ai_config, args.export_path)
                print_statistics(statistics)
                return 0
            except Exception as e:
                logger.error(f"第 {round_num + 1} 轮游戏运行出错: {str(e)}", exc_info=True)
                save_checkpoint(round_num, statistics)
                continue
        
        # 打印最终统计结果
        print_statistics(statistics)
        
    except FileNotFoundError as e:
        logger.error(f"配置文件不存在: {str(e)}")
        print(f"\n错误：配置文件不存在 - {str(e)}")
        print("请确保已创建配置文件，或从示例文件复制:")
        print("  cp config/ai_config.example.json config/ai_config.json")
        print("  cp config/role_config.example.json config/role_config.json")
        return 1
    except json.JSONDecodeError as e:
        logger.error(f"配置文件格式错误: {str(e)}")
        print(f"\n错误：配置文件格式错误 - {str(e)}")
        return 1
    except Exception as e:
        logger.error(f"游戏运行出错: {str(e)}", exc_info=True)
        print(f"\n错误：游戏运行出错 - {str(e)}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
