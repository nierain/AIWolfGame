# 桥接 Worker 启动卡

把这份文件丢给一个**全新对话**里的 AI，它就能接管这局狼人杀里全部电脑玩家，而且它的上下文里没有任何历史牌局记忆。

## 一次性准备（已有服务可跳过）

```bat
启动狼人杀.bat，然后在网页设置中选择“Codex 文件桥接”
```

等价命令（工作目录 `D:\gibhub\GithubStar\AIWolfGame`）：

```bash
.venv/Scripts/python.exe panel_game.py --port 18765 --provider codex --reset --open-browser
```

服务地址固定 `http://127.0.0.1:18765`。**改了 `game/` 里的代码必须重启服务才生效。**

## Worker 的四条命令

```
.venv\Scripts\python.exe tools\bridge_worker.py wait --timeout 300
.venv\Scripts\python.exe tools\bridge_worker.py reply <task_id> '<JSON>'
.venv\Scripts\python.exe tools\bridge_worker.py batch --action <动作> --intents '<按座位号索引的JSON>' [--default '<兜底JSON>'] [--idle 30]
.venv\Scripts\python.exe tools\bridge_worker.py stats
```

- `wait` 阻塞等待下一个任务，打印该座位视角的紧凑视图；真人在操作期间会 TIMEOUT，再等一次即可。
- `reply` 写回行动，会先校验动作类型、目标合法性和必填字段。
- `batch` 只用于**机械动作**（上警/退水这种没有内容的二选一）。**严禁**用它一次给多个座位写发言或投票——
  那等于让 11 个人共用一个脑子，发言会高度同质、票型会出现 10:1 这种真实牌桌不可能的整齐度（2026-09-15 已被真人当面指出）。
- `show` 打印的紧凑视图就是那个座位的全部认知来源。
- JSON 用单引号包住，**内部不要出现英文双引号**，引用请用「」。

## 决策隔离协议（最重要的一条）

**一次行动 = 一个独立决策者。** 每个电脑玩家只能看到公开发言（history）加上它自己那份私有信息，然后**自己分析**。
Worker 不许把 A 座位的推理、结论、立场带给 B 座位，更不许一次性替整桌规划立场分布。

落地方式（二选一）：

- **方式 A：每个任务交给一个全新上下文的子 Agent 决策。** 拿到 task_id 后起一个干净的子 Agent，只给它一条指令：
  "用 `tools/bridge_worker.py show <id>` 读你自己的视角，想清楚后用 `reply` 写回；不许读存档、不许读别人的任务、不许问别人"。
  子 Agent 没有前文记忆，只能靠公开发言判断——这才是"看到彼此的想法"的正确形态（想法只以发言的形式存在）。
- **方式 B：直接换 `--provider cli --cli codex`。** 每个行动一次独立调用，天然零共享上下文；
  而且骑士决斗在这个模式下是有效的（`human_game.py:958` 只跳过 `codex`，不跳过 `codex_cli`）。
- **方式 B'：换 `--provider cli --cli <ollama|claude|gemini>`。** 同样是每个行动一次独立进程，
  但用的是本机其他 CLI 模型；输出结构、提示词和合法性校验与 Codex CLI 完全一致。

还要守住：

- 不要为了写发言去读身份表。Worker 允许知道全场底牌，但"顺着底牌写出来的发言"会被真人一眼看出整齐感。
- 每个座位必须先用自己的私有信息（女巫记得自己救过谁、守卫知道自己是谁、预言家有自己的验人结果），再分析公开发言。
  忽略私有信息是最容易被真人看出来的"呆"。

## 铁律

1. 每个座位的行动，**只能**基于该任务里的 `visible_state`，不得串用其它座位的信息。
   真人明确表示：Worker 本人知道全场底牌没关系，要求的是**每个 AI 玩家的知识面严格受限**。
2. **禁止**向真人播报电脑玩家的身份、查验结果、夜间私密行动（谁被救/被守/被魅惑）、狼队友名单。只播报真人本来就看得见的公开动作。
3. 真人需要操作的阶段会暂停，不要替他决定。

## 注意

- 一个 Agent 包 11 个座位，处理任务时必然会看到每个座位自己的视角，一场下来会攒出全场底牌——真人接受这一点，只要别串用、别剧透。真要"连 Worker 都零记忆"，改用 `--provider cli --cli codex`（每次行动独立调用、互不共享上下文）。
- 真人希望被称呼为 **rain**。
- 本机坑：bash 缺 coreutils；PowerShell 工具沙箱报 Access Denied；`tasklist`/`netstat` 输出是 GBK。
