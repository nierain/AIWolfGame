# 项目长期约定（AIWolfGame）

## 陪玩质量：AI 玩家为什么显得"呆"（2026-09-15 真人反馈）

真人的原话是"这局玩家也太呆了吧，真的不是有上帝视角吗"。结论：**不是上帝视角，是"一个脑子开 11 个分会场"**。
决策机制本身是干净的（每个任务只带那个座位的 `visible_state`），呆来自这几条：

1. **批量写作导致同质化（最根本）**。用 `batch` 一次给 11 个座位写发言，等于同一时刻、同一套判据产出 11 段话，
   用词结构和立场高度相关，最后投出 10.5:1 这种真实牌桌不可能出现的票型。
   → 改进：强制立场分歧（每天必须有若干座位判断错/保悍跳/摸鱼），或直接换 `--provider codex-cli`
   （每次独立调用、互不共享上下文，天然更分裂）。
2. **神职不用自己的私有信息**。女巫救了某座位，却完全没用"我知道谁是被刀位"这条；
   真守卫眼看着假守卫跳出来也不对。→ 改进：Worker 规约里加"每个座位必须先用自己 private 里的信息"。
3. **不能为了写发言去读身份表**。Worker 允许知道全场底牌，但读了之后写出来的人是"顺着底牌写的"，
   会被真人看出整齐感。→ 除非必要，写发言时也只用该座位的 `visible_state`。
4. **骑士决斗在桥接模式下被代码关掉**（`human_game.py:958` 对 `name == "codex"` 直接 return False），
   真人喊骑士时毫无反应，看起来像呆。这是功能缺口，不是智力问题。

## 陪玩时的信息边界（重要）

- 需求方（rain）的明确要求是：**Worker 本人可以知道全场底牌，但每个 AI 玩家不能**。
- 因此：写回任何行动时，只能基于该任务文件里的 `visible_state`；不得把 A 座位的私有信息用到 B 座位的决策里。
- **禁止**向真人播报：电脑玩家身份、查验结果、夜间私有行动（谁被救/谁被守/谁被魅惑）、狼队友名单。
  只播报真人本来就看得见的公开动作（发言、投票、上警、决斗、公开死亡）。
- 排查环境问题优先用 `/api/health`；读存档看身份对判断没帮助，还容易顺手说漏嘴（2026-09-15 就因此作废过一局）。

## 桥接模式下让 AI 骑士真的能决斗（2026-09-17 实战中修复）

原代码 `human_game.py:_maybe_ai_knight_duel` 有一句 `or getattr(self.provider, "name", "") == "codex"`
直接 return False，导致桥接模式下骑士永远是一张哑牌。rain 要求实战中改掉，已改为只检查 `knight_used`。

**必须知道的机制**：这个决斗走的是**同步调用**——引擎在**每次公开发言之后**立刻问骑士"要不要动手"，
等不了异步回写。所以桥接模式要让骑士真动手，**必须在引擎发问之前预写好回应**：

```
task_id = sha256(f"{game_id}|{day}|{phase}|{seat}|knight_decide|{sequence}")[:24]
sequence = state["action_seq"] + 1      # 发言被应用时就地取值，_apply 不改 action_seq
```

把 `{"action":"knight_decide","target":<座位或null>}` 写进 `codex_bridge/responses/<task_id>.json` 即可。
没预写 → 同步调用返回 None → 视作"这一拍不决斗"（只留一个孤儿任务文件，无副作用）。
**所以每提交一次发言，就要为下一拍重新预写一次。**

改完**必须重启 `panel_game.py`**（模块已在内存里）。重启安全：`load_state()` 每次请求都从磁盘读，
不加 `--reset` 就不会清档。实测重启后 phase/day/警长/action_seq/待办 全部完好。

**教训（2026-09-17 实战踩到）**：用脚本按"座位→目标"表批量投票时，**必须核对表里覆盖了每一个投票座位**。
我漏了 9 号，脚本兜底逻辑 `if target not in allowed: target = allowed[0]` 就让它投了 1 号，
而它自己的公开立场是归票 4 号。结果没被改变（4 号仍以 7:4 出局），但记录里多了一票自相矛盾的票。
这正是"投票不该机械化"那条规矩的实证：**每张票都要回到该座位自己的立场上检查一遍**。

## 陪玩约定：真人出局后仍然指挥狼队（2026-09-17 rain 定）

引擎的默认行为是：真人一旦不在存活狼队里，`_start_night` 的狼队队列就变成存活的电脑狼
（`if state["human_seat"] in wolves` 不成立），狼聊和刀人全交给 AI。
但 rain 要求**他出局后仍然由他指挥狼队**，所以实际操作是：

- 每个夜晚我把狼队视角（狼聊上下文、可选刀口）摊给他；
- 他给**方案**（刀谁、白天怎么站、要不要切割），我把它落到 2、11、12 三只狼的 `wolf_chat` 上
  （一个指挥给方案 + 其余狼认领/微调，这正是引擎对真人当"狼队指挥"的原始设定）；
- 刀口按他的指示写回；
- **好人阵营（1、3、5、6、7、8、9、10）仍然由我按各自视角独立打，不向他透露任何信息。**

这不破坏隔离：他出局后掌握的信息只有公开信息 + 他对队友的了解，指挥狼队不会多出他不该有的东西。

## ⚠️ 身份公示规则：被放逐/被刀 **不翻牌**（2026-09-17 我搞错过一次）

**只有三种情况会公开身份**：
1. **骑士决斗** —— `_knight_duel` 会写 `revealed_role` 并公告"公开身份为X"；
2. **狼美人死亡** —— 殉情要公告绑的是谁，狼美身份跟着暴露；
3. **发言/遗言里自己认**。

**被放逐、夜里被刀，都不公示。** 校验方法：看该座位的 `players[seat]["revealed_role"]`
（`None` 就是没公示）以及 history 里有没有"公开身份"这种文案。

**我犯的错**：2026-09-17 那局我把"放逐翻牌"这个**别的实现的常见约定**套上来了，
在第 2 天的 8 段 AI 发言里全写成"4号翻牌是狼"——那是一条 AI 不可能知道的信息，
直接违反项目最核心的隔离原则。rain 一眼看出问题（"难道死了还会暴露身份吗"）。

**补救做过一次，方法可复用**（回滚到某天开始前、保留夜间结果）：
1. 剪 `history` 到目标边界（按 `event["id"]`），同步改 `event_seq`；
2. 清 `pending` / `phase_data`，把 `action_seq` 设成目标请求的前一号；
3. **调引擎自己的 `_begin_speech_queue(state, "forward")` 重建队列**——不要手搓 `phase_data` 结构；
4. `assert` 校验（phase / queue / index / pending.actor / sequence / 夜间结果是否保留）后再原子替换写盘；
5. **必须删掉该天的 `codex_bridge/tasks|responses`**——序列号一致，否则旧发言会被原样重放。
写回用"临时文件 + `replace()`"做原子替换，校验不过就不动原档（rain 表示不用额外备份）。

**回滚最容易漏的一步：AI 记忆里的残留。** 只删 history 是不够的——`_remember()` 会把每段发言写进
`ai_memories[seat]["own_speeches"]`、每张票写进 `["votes"]` 并把目标追加到 `["beliefs"]["suspects"]`，
而这些会通过 `compact_memory()` 进入 AI 的可见 `memory`。**AI 会"记得自己说过"被删掉的那段话。**
所以回滚后必须同时清：①`history` ②`own_speeches` ③`votes` ④按剩余投票重建 `beliefs.suspects`
⑤该天的 `codex_bridge/tasks|responses`（任务文件里的 `visible_state` 自带 history 和 memory，
残留文件会把脏内容一起带回来）。

**回滚后必须用引擎自己的 `get_visible_state()` 逐座位验一遍**（这才是权威口径，不要只 grep 文件——
"翻狼"这类词在正常语境里也出现，直接 grep 全是误报）。判据：
该座位眼里 `players[4].role` 是否为 `None`、有没有命中污染短语、`memory` 里有没有该天的残留。
注意狼队友看到 4 号是 `werewolf` 是**合法的**（他们本来就该知道队友）。

**另一个坑**：`_resolve_night()` 会把 `state["day"]` 加一。回滚到某个夜晚再让它重新结算，
day 会被多加一次——必须在回滚时先把 day 退回，并同步修正当天事件的 `day` 标签和
`seer_checks` 里 `{"day": state["day"] + 1}` 的记录。

## 引擎小 bug：警长票被记成"怀疑"（2026-09-17 实战发现，未修）

`human_game.py:151-155` 的 `_remember(..., "vote", value)` 会把投票目标追加进
`memory["beliefs"]["suspects"]`，且**不区分票的性质**。于是**警长票（一张"支持"票）也被记成"怀疑"**——
实测：10 号把警长票投给 9 号后，它的 `memory.怀疑` 变成 `[9]`，方向完全反了。
1 号、3 号同理（都投了 9 → 怀疑=[9]）；2 号投 4 → 怀疑=[4]。

影响：AI 的可见 `memory.怀疑` 与它自己的实际行动矛盾（刚支持的候选人变成"怀疑对象"），
可能让后续发言/投票出现前后不一致。修法：`_remember` 增加 `counts_as_suspicion` 之类参数，
警长票（`phase == "sheriff_vote"`）只记 `votes`、不写 `suspects`。
**没在本局中途修**——改了会立刻改变 AI 行为，等这局结束再动。

## 页面 bug：轮询撞上真人回合时不刷新（2026-09-17 实战发现并修复）

**症状**：AI 发言看起来"卡住"没显示。实测案例——9 号警上发言其实**已经入库**（history #9），
游戏也正确推进到 4 号（真人），但面板还停在被消费前的样子，并显示一句红色报错
「当前必须等待真人操作」，让人以为卡在 9 号。

**根因**：页面轮询 `继续`；当某个 AI 动作被消费、下一位恰好是真人时，
下一次轮询会调用 `advance_ai` → 抛「当前必须等待真人操作」→ HTTP 400。
而 `run()` 的 catch 只写了 `$('error').textContent = e.message`，**不刷新 state** →
页面永远拿不到那份"已推进到真人回合"的最新状态。

**修复**（`web/panel_game.html` 的 `run()`）：
```js
catch(e){const msg=String(e&&e.message||'');
  if(/轮到你|必须等待真人/.test(msg)){$('error').textContent='';
    $('actor-hint').textContent='轮到你操作了。';await refreshState()}
  else{...}}
```
新增 `refreshState()`：GET `/api/state` 并 `render()`。刷新后 `can_human_act` 为真，
`scheduleBridgePoll` 自然提前 return，不再产生 400 刷屏。

**验证方式**：DOM 桩 + 真实页面脚本，桩 `fetch` 让 `/api/action` 返回 400 而 `/api/state` 返回正常状态，
断言"最后两步是 action → state、第二步是 GET、state 已更新、没有把提示当红色报错"。5 项全过。

## 信息隔离的真相：三个泄露面（2026-09-17 审计）

rain 问"你确定桥接模式不会通身份？"。**要分三层回答，只有第一层是密封的。**

### 第一层：引擎 → AI 玩家的视角 ✅ 密封

`get_visible_state(state, seat)`（`human_game.py:780-849`）里，别的座位的 `role` 只在四种情况非空：
自己是那个座位 / 自己也是狼且对方是狼队友 / 对方 `revealed_role` 已公开 / `winner` 已定（赛后）。
顶层不返回 `roles`/`abilities`/`night`/`ai_memories`；`private` 只放该身份该有的键（平民是空字典）。

**审计结果：12 局完整对局 × 每步 × 全部 12 个座位 = 11424 个组合，零违规。**
已固化为常驻测试 `test_visible_state_never_leaks_a_role_it_should_not`，并做过变异检验
（把 `role` 改成恒返回真实身份 → 测试精确报出 `seed=0 phase=night_wolf_chat seat=1`）。
桥接和 codex-cli 用的是同一个函数，密封程度完全一样。

### 第二层：磁盘 ⚠️ 真实泄露面

`codex_bridge/tasks/` 是**故意持久化**的（要等人来取），实测 54 个文件里 **38 个含 private**：
`wolf_teammates` / `seer_checks` / `tonight_wolf_target` / `medicine` / `poison` / `last_guarded`。
**任何人打开这个目录就知道谁是狼、女巫救没救、预言家验了谁。**
codex-cli 反而没有这个问题——它用 `tempfile.TemporaryDirectory`，进程退出自动删。
好消息：`codex_bridge/tasks/` 已在 `.gitignore:204`，不会误提交。
缓解：`--bridge-dir` 指到仓库外的临时目录，或玩完手动清理。

### 第三层：Worker（本 Agent）本人 ⚠️ 纪律问题，代码管不了

一场下来必然攒齐全场底牌（rain 明确接受）。代码无法阻止跨座位串用。
另外 `bridge_worker.py show <id>` 会把 `PRIVATE 狼队友 [5, 10, 12]` **直接打在终端上**——
录屏/共享终端/日志留存都会泄露。但 `show` 必须给 Worker 看私有信息才能做好决策，不能简单隐藏。

## 真人坐在狼队时，狼聊和刀人只由真人决定（2026-09-17 实测）

`human_game.py:255-257`（`_start_night`）和 `:355-357`（`_start_next_queue`）都有这段：

```python
wolves = [seat for seat in _alive(state) if _role(state, seat) in WOLF_ROLES]
if state["human_seat"] in wolves:
    wolves = [state["human_seat"]]
```

**含义**：真人一旦是狼阵营，`night_wolf_chat` 和 `night_wolf_vote` 的队列里**只有真人一个人**，
电脑狼既不参与狼聊、也不投票定刀口。所以：
- 桥接模式下这两步**不会产生任务文件**，Worker 会以为"狼队没动"（我第一次就困惑了半天）。
- 真人每个夜晚有 **2 个自己的动作**（狼聊 + 刀人），其余夜间动作（魅惑/守护/查验/用药）才是 AI 的。
- 所以"一句话让 Agent 打完整局"这个设想**不成立**：真人自己的回合必须穿插进来。

配套结论：**Agent 只能被"收到一条消息"触发**，没有别的唤醒途径（定时自动化最细是 HOURLY，粒度不够）。
所以真正可行的节奏是——真人每做完自己那一回合，发一句"继续"，
Agent 把他之后的所有 AI 动作一口气打完，直到下一个真人回合。一天约 2~3 次，而不是几十次。

## 自动推进（2026-09-17 加入）

页面**默认一次点击只推进一位电脑玩家**（为了让真人读完每段发言），所以"没人点"就推不动。
现在有两条路，都不要我参与：

1. **页面标题栏的开关**（首选）：`▶ 自动推进` + 间隔下拉（2/4/8/15 秒），设置存 localStorage。
   逻辑在 `scheduleBridgePoll()`（`web/panel_game.html`）：`can_human_act` 时**先 return**，
   再走原有的 `waiting_external` / MVP 分支，最后才是 `autoAdvance` 分支。
2. **`tools/auto_advance.py`**：循环 POST `{"action":"continue"}`。适合不开浏览器窗口时用，
   但**必须给面板指定固定端口**（`--port 0` 是随机端口，脚本找不到）。

**改页面交互逻辑后怎么验证**（没装浏览器时的办法）：把 `<script>` 内容抽出来，在 Node 里配一套
DOM 桩（`document`/`localStorage`/`location`/`fetch`/`navigator` + 返回 Proxy 假元素），
再把测试代码**追加到同一份脚本末尾**——这样就能直接读写同一作用域里的 `state` / `bridgeTimer` /
`scheduleBridgePoll`，断言真实的调度规则。比引入 agent-browser（要下 500MB Chromium）划算得多。
本机**没有装 agent-browser**，别假设能用。

## Provider 命名对照（容易混，跟 rain 沟通时用全名）

项目里有 5 个 `--provider` 取值，名字起得很像，2026-09-17 就因为"cli 模式"这个词让 rain 困惑过一次：

| 取值 | 是什么 | rain 会怎么叫它 |
| --- | --- | --- |
| `codex-cli` | 调本机 Codex CLI，吃 **ChatGPT 账号额度**。`启动狼人杀.bat` 的默认值 | "和 codex 玩" ← **就是他一直在玩的** |
| `cli` | 通用 CLI 容器，需要本机装 ollama/claude/gemini 之一 | 我口中的"cli 模式/路线 B" |
| `codex` | 文件桥接，等外部 Worker 写 `responses/<id>.json` | "桥接模式"（他明确表示不想玩） |
| `api` | HTTP API Key（OpenAI 兼容） | 我口中的"路线 C / apikey 模式" |
| `builtin` | 内置离线 AI，无依赖 | "离线 AI" |

**说"cli 模式"时一定要区分是 `codex-cli` 还是 `cli`。** rain 说"和 codex 玩的那局"= `codex-cli`。

**额度是账号级的**：套一层 `cli` 去调 codex 也躲不过 ChatGPT 额度限制，不要建议这条路。

## Provider 层约定（2026-09-17 加固）

- **`ActionProvider` 只有一个抽象方法** `request_action(visible_state, request) -> dict | None`，
  返回 `None` 表示"等外部 Worker 写回"（`CodexFileProvider` 用这个语义）。加新后端只需实现它。
- **与后端无关的资产一律放在 `game/ai_providers.py` 模块级**，不要重新实现：
  `action_schema(action)`（输出结构）、`action_prompt(action)`（角色提示词）、
  `validate_intent(intent, request, label)`（合法性校验）、`parse_action_payload(raw)`（从任意输出里抠 JSON）。
  `CodexCLIProvider._prompt` / `._validate_intent` 保留为兼容入口，内部转调这些函数。
- 现有后端：`builtin`（离线）、`codex`（文件桥接）、`codex-cli`（Codex CLI）、
  `cli`（通用 CLI，见下）。新增后端请同步 `create_provider` 与 `panel_game.py --provider` 的 choices。
- **`cli` 模式**：`CliBackend` 用声明式模板描述后端（executables / template / prompt_mode / model_flag），
  `build_argv()` 是纯函数、不需要装 CLI 就能测。已接 `ollama`(stdin) / `claude`(stdin) / `gemini`(argv)。
  想加后端就在 `CLI_BACKENDS` 里加一条 `CliBackend`，别改 `GenericCLIProvider`。
- **`api` 模式**：标准 OpenAI 兼容 `/chat/completions`，**纯 urllib、零新依赖**。
  `load_api_profile()` 读 `config/ai_config.json` 的 `ai_players.<NAME>`，优先级
  **命令行参数 > `AIWOLF_BASE_URL`/`AIWOLF_API_KEY`/`AIWOLF_MODEL` > 配置文件**——
  所以只给 key、连文件都不用改也能跑。system 消息放提示词+输出结构，user 消息只放该座位 `visible_state`。
- 失败分两类，别混淆：`ProviderFailure` = 重试也没用（key 失效/额度/连不上/超时），**立即抛出**；
  普通 `ValueError` = 这次输出不合法，**才走重试**。加新后端时请沿用这个区分，否则会空转重试。
- 面向用户的报错必须过 `summarize_cli_error()` / `summarize_api_error()`：CLI 和 API 都会把整段请求回显，
  直接截断会把真正原因埋掉（2026-09-17 就因此只看到一大段发言文本，看不到"额度用尽"）。
- **`GenericCLIProvider.name` 必须不等于 `codex`**：`human_game.py:1081` 按 provider 名字跳过 AI 骑士决斗。
  新后端同理不要起名 `codex`。（该硬编码本身待改成能力位，见 PROJECT_CONTEXT 下一步优先级。）
- **铁律不变**：一次调用 = 一个座位的 `visible_state` = 一个独立进程。合并座位省 token 会立刻
  重现 2026-09-15 那次"11 个座位一个脑子"的同质化问题。
- Windows 上用 `split_cli_args()` 切分参数，不要直接 `shlex.split`（POSIX 模式会吃掉 `C:\...` 的反斜杠）。

## 让本 Agent 陪玩（已闭环，rain 不要每次来喊「继续」）

**rain 的诉求**：可以用我打，但不想每次专门来发一句"继续"。

**闭环配方（2026-09-17 定）**：

1. 双击 `启动狼人杀-桥接.bat`（桥接模式，端口固定 18765，**不加 `--reset`**）。
2. **把页面标题栏的「自动推进」开关打开**（间隔 4 或 8 秒）。
3. 他只需要发一句「开始」，我在**同一个回合**里跑 `wait → 决策 → reply` 的长循环，一直打到对局结束。

为什么这样就够：
- 桥接模式下 `waiting_external` 为真时页面本来就会自动重试 `continue`（每 1.4 秒），
  所以"我在想"的期间不需要人点。
- 唯一的缺口是**每个动作被消费后 `waiting_external` 变回假、页面就停了**——「自动推进」开关
  正好补上这个缺口（`can_human_act` 时仍会提前 return，真人回合不受影响）。
- 我不需要被反复唤醒，只要被唤醒一次然后自己循环。

**注意**：一局大约 100+ 次决策。`show` 的输出每个 2~4KB，全部塞进我的主上下文会爆。
所以长循环里**必须给每个任务起独立子 Agent**（BRIDGE_WORKER.md 的"方式 A"）：
只把任务号给子 Agent，让它自己 `show` / 决策 / `reply`，返回一行摘要。
这样我的上下文每步只涨一行，而且顺带拿到**真正的按座位隔离**（比今天"一个脑子写全场"更好）。
代价：子 Agent 调用次数多，token 消耗不小，要提前跟 rain 说清楚。

## 让本 Agent 直接陪玩（2026-09-17 已验证）

### ❌ 死路："cli 模式和我玩"（把本 Agent 当 CLI 后端调用）

2026-09-17 认真验证过，**走不通，别再试**：

- CLI 入口确实存在：`D:\新建文件夹\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy`（`cbc` 同名）。
  参数也齐全：`-p/--print`、`--output-format json`、`--json-schema`、`--tools ""`、`--no-session-persistence`、
  `--max-turns`、`--model`。看起来是个完美的 CLI 后端。
- 但实测三次全部卡死：一次后台跑满 10 分钟零输出，一次 90 秒超时零输出。
- 开 `-d api,hooks` 抓到根因：**`listen EADDRINUSE: 127.0.0.1:56677`**。
  那个端口是 **`WorkBuddy.exe`（宿主主程序）** 占的，也就是跑这个会话的进程。
  嵌套实例抢不到端口 → 未处理的 Promise rejection → 不报错、不退出、不返回。
- **结论：这是架构性冲突，不是配置问题。** 本 Agent 无法作为可调用的子进程存在。

推论（跟 rain 解释时用得上）：**"cli 模式和我玩" 与 "文件桥接" 本质是同一件事**——
游戏把任务交给我、我把结果交回去，只是介质不同（命令行 vs 文件）。命令行这条路被封死，
所以**想让我本人上场，只剩文件桥接**。

### ✅ 活路：文件桥接（见下）

启动（不要加 `--reset`，才能续上 rain 已有的那局）：

```
启动狼人杀-桥接.bat          # 等价于 panel_game.py --port 18765 --provider codex
```

然后我这边循环：`tools/bridge_worker.py wait --timeout N` → `show <id>` → `reply <id> '<json>'`。
- `pending` 打印的 id **不带 `.json`**；`show` / `reply` 也只吃不带后缀的 id。
- `reply` 的 JSON 用单引号包住，内部引用用「」，避免英文双引号。
- 只有 `wait` 拿到任务才动手；真人操作期间 `wait` 会 TIMEOUT，重等即可。
- 只要写了 `responses/<id>.json`，运行中的服务下一个 `continue` 就会采用，**不需要重启**。

**跨 provider 续局已实测（2026-09-17）**：codex-cli 玩到一半的存档，直接换 `--provider codex` 就能接手，
真档无需任何转换（provider 是启动参数，不进存档）。验证时务必用副本：
`--state-file %TEMP%/副本.json`，用完核对真档的 `pending.sequence` 没变。

**判断"是否推进"别看 phase**：`day_speech` 要连续过 12 个人，消费一条发言后阶段名不变。
看 `len(history)` 或 `pending.sequence` 的增量。

**真人出局后整局都由 AI 驱动**（2026-09-17 实际遇到）：rain 的 12 号在第 3 夜被刀后，
`can_human_act` 一直是 False，发言、投票、夜晚全部落在 provider 上，直到最后的**赛后复盘发言和 MVP 票选**
才会重新轮到真人（那时 `pending.actor == human_seat`）。所以桥接 Worker 在中后段要连续处理几十个行动。

纪律（比技术更重要）：
- **一次行动 = 一个独立决策者**。绝不能为了省事把多个座位的发言/投票一次性写完（2026-09-15 就是这么被真人抓出"呆"的）。
  要更高隔离度就用"每个任务起一个全新上下文的子 Agent，只给它那个座位的 `show` 输出"。
- 每个座位先用自己的 `private`（女巫记得救过谁、预言家有自己的验人结果），再分析公开发言。
- 写发言不要为了对齐而读身份表；读存档看底牌对判断没帮助，还容易说漏嘴。

限制：
- 桥接模式下 AI 骑士决斗被 `human_game.py:1081` 关掉（`name == "codex"`），恢复需要改成能力位。
- 我一次要处理一个座位，整局轮次多、耗时长，别承诺"一口气打完"。

## 本地环境坑

- **改完 `game/` 或 `panel_game.py` 必须重启服务才生效**：Python 启动时已把模块读进内存，
  运行中的进程不会加载新代码（踩过一次：网页传来的新字段被旧进程忽略）。
- `panel_game.py` 的默认端口 8765 在本机被别的服务占用（返回 404），桥接模式固定用 18765。
- bash 环境缺 coreutils（`ls`/`cat`/`tail` 不存在）；PowerShell 工具沙箱会报 Access Denied；`wmic` 被安全策略禁用。
- `tasklist` / `netstat` 的中文输出是 GBK，读取要 `decode('gbk','replace')`。
- 用 `run_in_background` 起的服务可以跨对话轮次存活。
- 本机只装了 Codex CLI（`codex.cmd`），**没有** ollama / claude / gemini，所以 `cli` 模式的真机验证
  只能用"往 PATH 里塞一个桩 CLI + 临时注册 `CLI_BACKENDS`"的办法做端到端烟雾测试。
- **2026-09-17：Codex 额度已用尽，锁到 2026-10-17 11:28**（`You've hit your usage limit`）。
  在此之前 `--provider codex-cli` 每次行动都会失败。可用的替代只有 `--provider builtin`（离线、秒回、逻辑简单），
  或者装上 ollama/claude/gemini 走 `--provider cli`。
- `config/ai_config.json` 里 8 个模型（GPT4O/CLAUDE/DEEPSEEK/GEMINI/QWEN/GROK/LLAMA/KIMI）
  **全是 `your-api-key-here` 占位**，baseurl 也是 `your-api-endpoint.com`。要走 HTTP API 必须先让 rain 提供 key，
  并且我还没实现 `ApiProvider`（PROJECT_CONTEXT 下一步优先级 2）。
- **provider 是启动参数，不存进存档**：中途换后端（比如 Codex 挂了）可以用另一条 `--provider` 命令
  **继续同一局**，不会丢进度。出错的行动不写入存档，所以失败也不会污染对局。
- `--cli-args` 的值以 `-` 开头时，argparse 会当成新选项而报 "expected one argument"，
  必须用 `--cli-args=--xxx` 形式（已在 help 里写明）。
- **本机有 HTTP 代理**（`HTTP_PROXY=http://127.0.0.1:56704`），而且**连 `127.0.0.1` 的请求也会被它拦截**：
  本地服务没起来时会拿到 `HTTP 502 upstream connect failed`，很容易误判成服务端 bug。
  测本地端点时先在环境里设 `no_proxy=127.0.0.1,localhost`。真实的远程 API 走代理是正常的。
- 起本地 `ThreadingHTTPServer` 做测试时，`serve_forever()` 默认 0.5 秒轮询一次，
  `shutdown()` 要等这一轮才返回 → 每个用例白等 0.5 秒。用 `poll_interval=0.02`。
- **bash 里没有 `timeout`**：Windows 的 `TIMEOUT.EXE` 是"等待/暂停"命令，
  `timeout 90 python xxx` 会报"无效语法"。要限时就用 Python 的 `subprocess.run(..., timeout=N)`。
- 探测"谁占了某端口"：`netstat -ano -p TCP` 输出是 GBK，筛出 PID 再用
  `tasklist /FI "PID eq <pid>" /FO CSV /NH` 查进程名。
- 单次子进程启动约 0.35s（python）/ 0.52s（.cmd 包装），一次夜间 `advance_ai` 会连锁调用十几次，
  所以端到端测试不要驱动太多 AI 行动，让第一次决策走真子进程、其余回退内置 AI。
- bash 环境缺 coreutils（`ls`/`cat`/`tail` 不存在）；PowerShell 工具沙箱会报 Access Denied；`wmic` 被安全策略禁用。
- `tasklist` / `netstat` 的中文输出是 GBK，读取要 `decode('gbk','replace')`。
- 用 `run_in_background` 起的服务可以跨对话轮次存活。
