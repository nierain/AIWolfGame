# AI狼人杀模拟器 🤖🐺

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-green.svg)](https://openai.com)

一个基于大语言模型的多智能体狼人杀项目，同时提供“1名真人 + 11名电脑玩家”的本地网页模式。项目保留原有全 AI 模拟器，并新增由确定性规则引擎裁决的预女骑守 + 狼美人 12 人局。

## 📋 目录

- [功能特点](#功能特点)
- [快速开始]( 
- [详细配置](#详细配置)
- [游戏机制](#游戏机制)
- [项目结构](#项目结构)
- [API文档](#api文档)
- [常见问题](#常见问题)
- [更新日志](#更新日志)

## ✨ 功能特点

### 🎮 游戏功能
- **真人完整参与**：座位可随机或指定，发言、投票、警长竞选和身份技能均由真人操作
- **正式12人板**：3狼人、狼美人、预言家、女巫、骑士、守卫、4平民，采用屠边规则
- **第二块板「镜隐迷踪」**：3小狼 + 觉醒隐狼 vs 魔镜少女 + 守卫 + 女巫 + 猎人 + 4平民（详见[游戏机制](#游戏机制)）
- **刷新可恢复**：每次操作自动保存，浏览器刷新或程序重启后可继续
- **真人固定形象**：真人玩家名为“牢雨”并使用独立头像，与电脑玩家一起显示在座位区和当前行动区
- **多种角色支持**：狼人、村民、预言家、女巫、猎人、白痴、守卫、骑士等
- **灵活人数配置**：支持6-12人局，每种人数提供多种预设配置
- **完整游戏流程**：夜晚行动、白天讨论、投票处决、遗言发表
- **赛后全员复盘**：胜负确定并公开身份后，12名玩家依次分享感想、关键判断和全局思路，并评价2至4位印象特别的选手
- **全员MVP票选**：复盘结束后每人秘密投一票并附简短理由，可公正自投；统一公开票型，同票并列当选
- **平票处理**：平票时进入补充发言阶段并重新投票
- **MVP/SVP评选**：每局结束后评选胜方MVP和败方SVP

### 🤖 AI系统
- **多模型支持**：GPT-4、Claude、Gemini、DeepSeek、Qwen、Grok、Llama、Kimi等
- **角色认知**：AI清楚自己的角色身份和阵营目标
- **固定玩家档案**：12名电脑玩家各自拥有固定姓名、头像、人格和说话风格；每局随机抽取11名，换座位时身份档案会一起移动
- **压缩长期记忆**：完整对局本地封存，每名电脑玩家只把胜负统计、最近对局摘要和自己的赛后复盘带入下一局，避免重复消耗模型上下文
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

Windows 用户最简单的方式：直接双击项目根目录的 `启动狼人杀.bat`。它会使用本机已经登录的 Codex 控制 11 名电脑玩家，自动选择一个可用端口、启动游戏并打开正确网页。不要直接双击 `web/panel_game.html`，因为网页文件本身无法读取本地游戏状态。

现在只有一个启动入口。打开网页后，在开始游戏设置中选择三种模式之一：本地 CLI（再选 Codex/Ollama/Claude/Gemini）、Codex 文件桥接、OpenAI 兼容 API。

```bash
python panel_game.py --open-browser
```

打开 `http://127.0.0.1:8765`。开始时可选择随机座位或指定 1~12 号；身份默认随机，也可在调试下拉框中指定。轮到真人的任何操作时游戏都会暂停，电脑发言则点击一次推进一位。

`cli` 模式允许用户选择本机 CLI 后端：`codex`、`ollama`、`claude` 或 `gemini`。每个电脑座位只会收到该玩家理论上可见的状态；模型只负责决定行动，游戏程序继续负责规则和结算。模型发言和决策需要等待一段时间，也会消耗对应模型或账号的额度。

项目默认把 Codex CLI 的 `CODEX_HOME` 固定到桌面端共享的 `~/.codex`，因此会沿用当前桌面端 ChatGPT 账号。只有设置 `AIWOLF_CODEX_HOME` 时才会使用另一个 CLI 账号目录。

额度用尽时不会毁掉存档：出错的行动不会写入，重新打开同一个入口并在设置中换后端即可**继续同一局**。选择的模式配置会随存档保存，但不会保存 API 密钥。

游戏进度会自动保存在 `.panel_game_state.json`；关闭程序后再次运行即可续局。返回设置页可开始新局，也可用以下命令强制回到设置页：

```bash
python panel_game.py --reset --open-browser
```

保留的实验性 Codex 文件桥接模式：

```bash
python panel_game.py --provider codex
```

该模式把每个 AI 的隔离视角任务写到 `codex_bridge/tasks/`，只读取 `codex_bridge/responses/` 中同名任务的 JSON 行动。它仍需要外部 Worker。

`--cli` 目前支持四个后端：

| 后端 | 启动方式 | 结构化输出 |
| --- | --- | --- |
| `codex` | 本机已登录的 Codex CLI | 使用 Codex 输出 schema 文件 |
| `ollama` | `ollama run <model> --format json` | `--format json` 强制合法 JSON，但要选上下文窗口够大的模型 |
| `claude` | `claude -p --output-format json --max-turns 1` | 输出是 `{result: ...}` 信封，程序会自动拆开 |
| `gemini` | `gemini -p <提示词>` | 提示词走命令行参数，超长局面可能撞上参数长度上限 |

`cli` 模式的所有后端共用同一份输出结构、角色提示词和合法性校验。每次行动仍然是一个独立进程、只携带该座位的可见状态，所以不会出现 11 个座位互相串味。返回不合法时会按 `--cli-retries`（默认 2）重试，仍失败就抛出明确错误，不会把脏数据写进对局。

其他参数：`--model`（模型名）、`--cli-timeout`（单次调用超时秒数，默认 180）、`--cli-args`（追加原始参数，值以 `-` 开头时须写成 `--cli-args=--xxx`）。

选择 API 模式后，它读取 `config/ai_config.json` 里第一个填好 `baseurl` 与 `api_key` 的条目，所以只要把该文件里对应模型的占位符换掉即可：

```json
{
  "ai_players": {
    "DEEPSEEK": {
      "baseurl": "https://api.deepseek.com/v1",
      "api_key": "sk-你的key",
      "model": "deepseek-chat",
      "timeout": 60
    }
  }
}
```

- 也可以用环境变量 `AIWOLF_BASE_URL` / `AIWOLF_API_KEY` / `AIWOLF_MODEL`，避免把 key 写进文件。

该模式走标准 OpenAI 兼容的 `/chat/completions`，用 `urllib` 实现，没有引入新依赖。系统消息里放角色提示词与输出结构，用户消息里只放该座位的 `visible_state`；单次调用约三到四千字符输入。与 `cli` 模式一样，每个座位一次独立请求、只带自己的可见状态，返回不合法会按 `--cli-retries` 重试。若机器上配置了 HTTP 代理，请求会走代理；要直连本地端点请设置 `no_proxy`。

#### 不想一直点「继续」：自动推进

页面的设计是**一次点击推进一位电脑玩家**，方便你读完每段发言。不想要这个节奏，有两种办法。

**一、页面右上角的开关（推荐）**

标题栏有 `▶ 自动推进：关` 和一个间隔下拉（2 / 4 / 8 / 15 秒）。打开后，只要当前是电脑玩家的回合，页面就会按你选的间隔自己往下走；**轮到你操作时会自动停下**，你操作完它继续。设置记在浏览器本地，刷新不丢。

想回头细读某一段，随时点开关暂停；本轮所有发言都还在公共记录里，可以往上翻或用发言按钮跳转。

**二、命令行工具（适合不想开浏览器窗口的场景）**

```bash
python tools/auto_advance.py --url http://127.0.0.1:18765
```

行为和开关一致：轮到你自己操作时自动让开，对局结束自动退出，并且**只打印天数和阶段、不打印任何身份**，所以那个终端本身也不会剧透。`--interval` 控制节奏（默认 1.6 秒），`--max-actions` 可以限个上限。

⚠️ 用命令行工具时**启动面板要指定固定端口**（例如 `--port 18765`）。如果用 `--port 0`，端口由系统随机分配，脚本找不到服务。

运行回归测试：

```bash
python -m unittest discover -s tests -v
```

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
| 狼人 | 狼人 | 夜聊/杀人 | 与狼队讨论并共同选择刀口 |
| 狼美人 | 狼人 | 魅惑 | 每晚魅惑一名非狼人；狼美人死亡时目标殉情 |
| 预言家 | 好人 | 查验身份 | 每晚可以查验一名玩家是否是狼人 |
| 女巫 | 好人 | 解药/毒药 | 可以使用解药救人或毒药杀人，各限一次 |
| 猎人 | 好人 | 开枪 | 死亡时可以开枪带走一名玩家 |
| 白痴 | 好人 | 免疫放逐 | 被投票放逐时不会死亡 |
| 守卫 | 好人 | 守护 | 每晚可以守护一名玩家免受狼人攻击 |
| 骑士 | 好人 | 决斗 | 可以与一名玩家决斗，如果对方是狼人则死亡 |
| 平民 | 好人 | 无 | 通过发言和投票帮助好人获胜 |
| 魔镜少女 | 好人 | 鉴真 | 每晚查验一名玩家的**具体身份**（女巫/守卫/狼人…，不是好人/狼人） |
| 觉醒隐狼 | 狼人 | 学习/带刀 | 第1晚学习一名玩家获得其身份+技能；与小狼互不知身份；小狼全灭后获得狼刀，学狼人时一晚双刀 |

### 板子：镜隐迷踪

第二块可选的 12 人板，设置页的「板子」下拉选择。

**配置**：狼人 ×3 + 觉醒隐狼 ×1；好人 = 魔镜少女 + 守卫 + 女巫 + 猎人 + 平民 ×4。屠边规则不变（神职全灭或平民全灭 → 狼胜）。

**觉醒隐狼**：属狼阵营，第 1 晚学习一名玩家（不能学自己，终身固定），获得其身份和技能：

- 学女巫 → 一瓶"救不活的毒"（被此毒击杀者，真女巫的解药也救不活）
- 学预言家 → 第 2 夜起每晚可查验
- 学守卫 → 可守护（且此守护能挡毒）
- 学猎人 → 死后可开枪
- 学平民 → 就是平民
- 学狼人 → 带刀时**一晚双刀**（两个不同目标）

隐狼与三只小狼**互不知身份**；只有入夜时场上小狼全灭，隐狼才获得狼刀。学狼人时带刀可双刀，学别的角色带刀只单刀。

**魔镜少女查验隐狼**：显示它**学到的角色**（学平民显示平民、学女巫显示女巫），不暴露它是隐狼。

**猎人**：死亡时可开枪带走一人；被放逐可开枪，被毒死不能开枪。

### 游戏流程

1. **夜晚阶段**
   - 狼人夜聊并分别提交击杀目标，由法官统计
   - 狼美人选择魅惑目标，守卫、预言家、女巫依次行动
   - 预言家查验玩家身份
   - 女巫选择是否使用解药或毒药

2. **白天阶段**
   - 公布夜间死亡信息
   - 存活玩家轮流发言
   - 进行投票，得票最多者出局
   - 平票时进入补充发言并重新投票

3. **游戏结束**
   - 狼人全部死亡：好人胜利
   - 神职全部死亡或平民全部死亡：狼人胜利（屠边）
   - 胜负确定后公开全部身份，所有玩家（包括已出局玩家）依次完成赛后复盘
   - 完成复盘后进行全员MVP票选，再将对局写入本地 `game_archives/`；压缩后的个人经验和对其他固定玩家的印象写入 `.aiwolf_long_term_memory.json`

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
│   ├── human_game.py          # 真人对局的确定性规则引擎
│   ├── ai_providers.py        # 可替换 AI/Codex 决策接口
│   ├── game_controller.py     # 游戏控制器
│   ├── ai_players.py          # AI玩家系统
│   └── roles.py               # 角色定义
├── utils/                      # 工具函数
│   ├── __init__.py
│   ├── game_utils.py          # 游戏工具函数
│   └── logger.py              # 日志系统
├── logs/                       # 日志目录（自动生成）
├── game_results/               # 游戏结果（自动生成）
├── tests/                      # 真人对局回归测试
├── AGENTS.md                  # 后续 Codex 开发约定
├── PROJECT_CONTEXT.md         # 当前进度与下一步
├── panel_game.py              # 本地网页服务
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
