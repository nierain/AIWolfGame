# AI狼人杀模拟器 🤖🐺

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-green.svg)](https://openai.com)

一个基于大语言模型的多智能体狼人杀游戏模拟系统。通过配置不同的AI模型（如GPT-4、Claude、Gemini等）作为玩家，实现完整的狼人杀游戏流程，支持6-12人局，包含多种角色和规则配置。

## 📋 目录

- [功能特点](#功能特点)
- [快速开始](#快速开始)
- [详细配置](#详细配置)
- [游戏机制](#游戏机制)
- [项目结构](#项目结构)
- [API文档](#api文档)
- [常见问题](#常见问题)
- [更新日志](#更新日志)

## ✨ 功能特点

### 🎮 游戏功能
- **多种角色支持**：狼人、村民、预言家、女巫、猎人、白痴、守卫、骑士等
- **灵活人数配置**：支持6-12人局，每种人数提供多种预设配置
- **完整游戏流程**：夜晚行动、白天讨论、投票处决、遗言发表
- **平票处理**：平票时进入补充发言阶段并重新投票
- **MVP/SVP评选**：每局结束后评选胜方MVP和败方SVP

### 🤖 AI系统
- **多模型支持**：GPT-4、Claude、Gemini、DeepSeek、Qwen、Grok、Llama、Kimi等
- **角色认知**：AI清楚自己的角色身份和阵营目标
- **记忆系统**：AI会记录游戏历史，进行推理分析
- **智能投票**：支持弃票，API错误时自动处理

### 📊 统计功能
- **胜率统计**：追踪每个AI模型扮演不同角色的胜率
- **投票分析**：记录投票准确率、无效投票率
- **游戏记录**：完整保存游戏过程，支持复盘分析

## 🚀 快速开始

### 环境要求

- Python 3.8+
- OpenAI API兼容的接口（支持voapi等代理服务）

### 1. 克隆项目

```bash
git clone https://github.com/hikariming/AIWolfGame.git
cd AIWolfGame
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置AI模型

复制示例配置文件：

```bash
cp config/ai_config.example.json config/ai_config.json
cp config/role_config.example.json config/role_config.json
```

编辑 `config/ai_config.json`，配置你的API密钥：

> [!NOTE]
>
> 此处 `show_reasoning` 不会传递给 API，只作为控制程序是否输出思考内容时使用。
> 
> 部分大模型具有深度思考功能，在输出最终回答之前，模型会先输出一段思维链内容，以提升最终答案的准确性。`show_reasoning` 选项用于控制程序是否将模型的思考内容展示出来。
>
> 考虑到部分模型思考较为简单，而部分模型思考较为冗长，本选项分模型设置。每个模型都有对应的 `show_reasoning` 选项，可以单独控制每个模型思考输出与否。
> 
> 如有传入 extra_body 的需要，可直接添加一项 `extra_body`，内容将原样传递给模型。例如，控制 DeepSeek 思考模式的开关，可以这样写：
> 
> ```json 
> "DEEPSEEK-PRO-REASONING": {
>       "baseurl": "https://api.deepseek.com",
>       "api_key": "sk-*************************",
>       "model": "deepseek-v4-pro",
>       "extra_body": {"thinking": {"type": "enabled"}},
>       "retry_attempts": 3,
>       "timeout": 30,
>       "show_reasoning": true
>     },
> ```
> 
> 其他模型的其他 `extra_body` 参数，可在各模型的官网中详细查看。

```json
{
  "evaluation_settings": {
    "models_to_evaluate": ["GPT4O", "CLAUDE", "DEEPSEEK"],
    "export_format": ["json"]
  },
  "ai_players": {
    "GPT4O": {
      "baseurl": "https://your-api-endpoint.com/v1",
      "api_key": "your-api-key-here",
      "model": "gpt-4o-2024-11-20",
      "retry_attempts": 3,
      "timeout": 30,
      "show_reasoning": true
    }
  }
}
```

### 4. 运行游戏

#### 本地交互面板（1名真人 + 11名电脑玩家）

```bash
python panel_game.py
```

打开 `http://127.0.0.1:8765`。面板会逐个展示发言、保留完整历史，并在轮到1号时接受真人发言；游戏进度会自动保存在本地。使用 `python panel_game.py --reset` 可重置面板对局。

#### 方式一：自动选择（推荐）

```bash
python main.py --rounds 1 --delay 0.5
```

系统会根据配置的模型数量自动推荐可用的游戏人数。

#### 方式二：指定人数

```bash
# 运行9人局
python main.py --preset 9 --rounds 1 --delay 0.5

# 运行12人局
python main.py --preset 12 --rounds 1 --delay 0.5
```

#### 方式三：调试模式

```bash
python main.py --preset 8 --rounds 1 --debug
```

### 5. 命令行参数

| 参数 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `--preset` | 选择人数局(6-12) | 自动询问 | `--preset 9` |
| `--rounds` | 运行轮数 | 100 | `--rounds 5` |
| `--delay` | 每步延迟(秒) | 1.0 | `--delay 0.5` |
| `--debug` | 调试模式 | False | `--debug` |
| `--resume` | 从中断处继续 | False | `--resume` |
| `--role-config` | 角色配置文件 | config/role_config.json | `--role-config custom.json` |
| `--ai-config` | AI配置文件 | config/ai_config.json | `--ai-config custom.json` |

## ⚙️ 详细配置

### AI模型配置

支持的模型类型：

| 模型 | 说明 | 推荐模型名 |
|------|------|-----------|
| GPT4O | OpenAI GPT-4 | gpt-4o-2024-11-20 |
| CLAUDE | Anthropic Claude | claude-3-7-sonnet-20250219 |
| DEEPSEEK | DeepSeek | deepseek-chat |
| GEMINI | Google Gemini | gemini-2.5-pro |
| QWEN | 阿里通义千问 | qwen-max-latest |
| GROK | xAI Grok | grok-3 |
| LLAMA | Meta Llama | llama3-70b-8192 |
| KIMI | Moonshot Kimi | kimi-k2-0711-preview |

### 预设游戏配置

#### 6人局
- **全网通用标准配置**：2狼人、1预言家、1女巫、2平民
- **官方极简变种配置**：2狼人、1预言家、3平民

#### 9人局
- **官方标准配置（预女猎白板子）**：3狼人、1预言家、1女巫、1猎人、1白痴、3平民
- **进阶变种配置-守卫局**：3狼人、1预言家、1女巫、1守卫、4平民

#### 12人局
- **新手入门标准板（预女猎白）**：4狼人、1预言家、1女巫、1猎人、1白痴、4平民
- **狼王守卫局**：3狼人、1狼王、1预言家、1女巫、1猎人、1守卫、4平民
- **石像鬼守墓人局**：3狼人、1石像鬼、1预言家、1女巫、1守墓人、5平民
- **白狼王骑士局**：3狼人、1白狼王、1预言家、1女巫、1猎人、1骑士、4平民
- **血月使徒猎魔人局**：3狼人、1血月使徒、1预言家、1女巫、1猎人、1猎魔人、4平民

## 🎲 游戏机制

### 角色技能

| 角色 | 阵营 | 技能 | 说明 |
|------|------|------|------|
| 狼人 | 狼人 | 夜间杀人 | 每晚可以杀死一名玩家 |
| 预言家 | 好人 | 查验身份 | 每晚可以查验一名玩家是否是狼人 |
| 女巫 | 好人 | 解药/毒药 | 可以使用解药救人或毒药杀人，各限一次 |
| 猎人 | 好人 | 开枪 | 死亡时可以开枪带走一名玩家 |
| 白痴 | 好人 | 免疫放逐 | 被投票放逐时不会死亡 |
| 守卫 | 好人 | 守护 | 每晚可以守护一名玩家免受狼人攻击 |
| 骑士 | 好人 | 决斗 | 可以与一名玩家决斗，如果对方是狼人则死亡 |
| 平民 | 好人 | 无 | 通过发言和投票帮助好人获胜 |

### 游戏流程

1. **夜晚阶段**
   - 狼人讨论并选择击杀目标
   - 预言家查验玩家身份
   - 女巫选择是否使用解药或毒药

2. **白天阶段**
   - 公布夜间死亡信息
   - 存活玩家轮流发言
   - 进行投票，得票最多者出局
   - 平票时进入补充发言并重新投票

3. **游戏结束**
   - 狼人全部死亡：好人胜利
   - 狼人数量 ≥ 好人数量：狼人胜利

### MVP/SVP评分

评分标准：
- 存活到游戏结束：+10分
- 获胜阵营：+20分
- 投票准确率：最高+10分
- 角色技能使用：+3~5分/次
- 发言活跃度：+1分/次

## 📁 项目结构

```
AIWolfGame/
├── config/                     # 配置文件目录
│   ├── ai_config.json         # AI模型配置（需自行创建）
│   ├── ai_config.example.json # AI配置示例
│   ├── role_config.json       # 角色配置（需自行创建）
│   ├── role_config.example.json # 角色配置示例
│   └── preset_configs.json    # 预设游戏配置
├── game/                       # 游戏核心逻辑
│   ├── __init__.py
│   ├── game_controller.py     # 游戏控制器
│   ├── ai_players.py          # AI玩家系统
│   └── roles.py               # 角色定义
├── utils/                      # 工具函数
│   ├── __init__.py
│   ├── game_utils.py          # 游戏工具函数
│   └── logger.py              # 日志系统
├── logs/                       # 日志目录（自动生成）
├── game_results/               # 游戏结果（自动生成）
├── test_all.py                 # 测试脚本
├── main.py                     # 主程序入口
├── requirements.txt            # 依赖列表
├── LICENSE                     # MIT许可证
└── README.md                   # 项目说明
```

## 📖 API文档

### GameController

游戏主控制器，管理游戏流程。

```python
from game.game_controller import GameController

# 创建游戏
config = {
    "game_settings": {"total_players": 9, "random_roles": True},
    "role_counts": {"werewolf": 3, "seer": 1, "witch": 1, "villager": 4},
    "players": {...},
    "ai_players": {...}
}

game = GameController(config)
game.run_game()
```

### BaseAIAgent

AI玩家基类，提供统一的AI接口。

```python
from game.ai_players import create_ai_agent
from game.roles import Werewolf

# 创建AI代理
role = Werewolf("player1", "小欧")
config = {"api_key": "xxx", "model": "gpt-4", "baseurl": "..."}
agent = create_ai_agent(config, role)

# 讨论
result = agent.discuss(game_state)

# 投票
vote_result = agent.vote(game_state)
```

## ❓ 常见问题

### Q: 运行时报错 "No module named 'openai'"
A: 请确保已安装依赖：`pip install -r requirements.txt`

### Q: API调用失败，显示503错误
A: 这是模型服务暂时不可用，系统会自动处理为弃票。可以尝试更换模型或稍后重试。

### Q: 如何配置多个AI模型？
A: 在 `ai_config.json` 的 `ai_players` 中添加多个模型配置，并在 `evaluation_settings.models_to_evaluate` 中列出要使用的模型键名。

### Q: 游戏人数不够怎么办？
A: 至少需要配置6个模型才能运行6人局。如果模型数量不足，请添加更多模型配置。

### Q: 如何查看游戏记录？
A: 游戏记录保存在 `logs/` 目录下，按日期分类。

### Q: 如何自定义角色配置？
A: 复制 `role_config.example.json` 为 `role_config.json`，修改其中的 `role_counts` 和 `players` 配置。

## 📝 更新日志

### v0.1.0-beta (2026-03-08)
- ✅ 基础游戏功能完成
- ✅ 支持6-12人局
- ✅ 支持多种角色：狼人、预言家、女巫、猎人、白痴、守卫、骑士等
- ✅ 支持12种AI模型
- ✅ MVP/SVP评选系统
- ✅ 平票处理机制
- ✅ 完整的游戏记录和统计

## 🤝 贡献指南

欢迎提交Issue和Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证开源。

## 🙏 致谢

- 感谢所有开源的大语言模型
- 感谢狼人杀游戏社区
- 感谢所有贡献者
