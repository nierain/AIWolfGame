# AIWolfGame 陪玩/维护手册（细节版）

MEMORY.md 只留铁律与索引，长流程放这里。按需读取。

## 1. 信息隔离的三个泄露面

1. **引擎 → AI 玩家：密封。** `get_visible_state(state, seat)`（`game/human_game.py`）里别的座位
   `role` 只在四种情况非空：自己是该座位 / 自己也是狼且对方是狼队友 / 对方 `revealed_role` 已公开 /
   `winner` 已定。顶层不返回 `roles`/`abilities`/`night`/`ai_memories`。
   审计过 12 局 × 每步 × 12 座位 = 11424 组合零违规，固化为
   `test_visible_state_never_leaks_a_role_it_should_not`（做过变异检验有效）。
2. **磁盘：真实泄露面。** `codex_bridge/tasks/` 故意持久化，里面带 `wolf_teammates`/`seer_checks`/
   `tonight_wolf_target` 等私有信息；已在 `.gitignore`。缓解：`--bridge-dir` 指到仓库外，或玩完清理。
   codex-cli 无此问题（用 `tempfile.TemporaryDirectory`）。
3. **Worker（本 Agent）本人：纪律问题。** 一场必然攒齐全场底牌（rain 明确接受），代码管不了跨座位串用。
   `bridge_worker.py show` 会把私有信息打在终端上，录屏/共享终端会泄露。

## 2. 陪玩纪律（每次都要守）

- **一次行动 = 一个独立决策者**。绝不能一次给 11 个座位写发言（2026-09-15 被真人抓出"太呆"的根因：
  同一时刻同一套判据产出 11 段话，最后投出 10.5:1 这种不可能出现的票型）。
- 每个座位先用自己的 `private`（女巫记得救过谁、预言家有自己的验人结果），再分析公开发言。
- 写发言不要为了对齐去读身份表；读存档看底牌对判断没帮助，还容易顺手说漏嘴。
- 批量投票/行动要**逐座位回到它自己的公开立场**检查一遍（曾因漏一个座位触发兜底逻辑投出自相矛盾的票）。
- **禁止向真人播报**：电脑玩家身份、查验结果、夜间私有行动（谁被救/被守/被魅惑）、狼队友名单。
  只播报真人本来就看得见的公开动作（发言、投票、上警、决斗、公开死亡）。
- **身份公示只有三种**：骑士决斗、狼美人死亡（殉情公告带出身份）、本人发言/遗言自己认。
  **被放逐、被刀都不翻牌**。校验看 `players[seat]["revealed_role"]` 是否为 None。
- 真人出局后仍由他指挥狼队（rain 定的）：把狼队视角摊给他、按他的方案写回狼聊/刀口；
  好人阵营仍由我按各自视角独立打，不向他透露任何信息。
- 真人坐在狼队时，`night_wolf_chat`/`night_wolf_vote` 队列**只有真人一人**（引擎如此设计），
  桥接下这两步不产生任务文件；他每夜有 2 个自己的动作。
- Agent 只能被"收到一条消息"触发（自动化最细粒度是 HOURLY）。可行节奏：真人做完自己那一回合发一句"继续"，
  我把之后的 AI 动作一次打完，直到下一个真人回合。

## 3. 回滚对局（改坏历史/AI 记忆后用）

核心：**只剪 history 是不够的，AI 记忆、桥接文件、`day` 计数都要同步**。

1. 剪 `history` 到目标边界（按 `event["id"]`），同步 `event_seq`。
2. **存活状态按保留的历史重算**：扫描保留段里 `kind=="death"` 且文本以"出局。"结尾的事件，
   解析开头座位号得到"应死者"，其余座位一律 `alive=True`。比逐个复活可靠（不管已经多推了几天）。
3. 清 `pending` / `phase_data` / `night`，`day` 退回目标夜晚的值，再**调引擎自己的
   `_start_night(state)` 重建夜晚链**——不要手搓 `phase_data`。
4. 同步修 `abilities`：回滚到的夜晚之前的状态（本例把误覆盖的 `hidden_wolf_learned` 改回、
   清 `hidden_wolf_checks`、清 `hunter_shots`、删 `_hunter_shots_pending`）。
5. 清 AI 记忆残留：`_remember()` 写进 `own_speeches`/`votes`/`notes`/`beliefs.suspects`，
   会经 `compact_memory()` 进入 AI 可见 `memory`。狼聊是 `notes` 里的
   `{"day": n, "speaker": .., "text": ..}`，按 `day` 精确删。
6. 清该时间点之后的 `wolf_chat` 条目（按 `item["day"]`）。
7. **删掉该时间点的 `codex_bridge/tasks|responses`**（序列号一致会原样重放旧发言）；
   若保持 `action_seq` 单调递增则新序列不会撞旧文件。
8. 原子写盘（临时文件 + `os.replace`），先备份（`.panel_game_state.before_*.json`）。
9. **动盘前检查静默**：连续读 `state` 文件 mtime，不变化说明没有请求在跑（`panel_game.py` 在持锁期间
   保存，若写到一半被覆盖回滚就白做）。
10. **验证要在副本上推演**：`deepcopy` 后用 `HumanGameEngine(BuiltinAIProvider())` 的 `advance_ai`
    一路推到目标回合，断言 phase/action/actor/prompt 符合预期；确认后再写盘。

**坑**：`_resolve_night()` 会把 `day` 加一，回滚到某夜晚重跑必须先把 `day` 退回，
并同步当天事件的 `day` 标签与 `seer_checks` 里 `{"day": state["day"] + 1}` 的记录。
**从已结束的对局回滚**时还要额外清：`winner`（不清 AI 会看到全场身份）、`mvp_votes`、`mvp_result`、
`postgame_impressions`、`last_exiled`，以及**守卫的 `abilities.guard_last`**——要退回到目标夜晚之前的值，
否则"不能连守"规则会让守卫行为与史实不一致（该值可从公共记录里的守人记录反推）。
**重新从磁盘读 state 的服务器不需要重启**（`load_state()` 每次请求都读盘），但**改了代码必须重启**。

### 场景 3b：让真人指定某个 AI 座位的行动（"接管刀口"）

不改代码，直接把行动喂给引擎内部方法：

```python
engine = HumanGameEngine(BuiltinAIProvider())
engine._apply(state, 5, {"action": "wolf_chat", "text": "……"})   # 以 5 号的口吻落一条狼聊
engine._apply(state, 5, {"action": "wolf_kill", "target": 4})     # 写回真人指定的刀口
```

`_apply` 结尾会自己 `_advance_queue`，所以两步之后状态就推进到了下一步（例如 `night_guard`，守卫交还 AI）。
`submit_human` 不能用在这里——它要求 `pending.actor == human_seat`。需要触发**非行动类**的规则操作
（自爆、决斗）时用模块级函数：`from game.human_game import _wolf_explode` → `_wolf_explode(state, seat)`。
**注意**：真人接管的这一拍要**先把面板停掉再写**，不然挡不住电脑狼抢先决定（页面轮询会立刻消费掉这一拍）；
写完再重启面板即可。**先停面板再动存档是硬规则**：任一面板实例的在途请求结束时都会写回它加载时的版本，
直接覆盖你的修改（2026-09-19 一晚上踩了两次）。

补充两条规则细节（接管夜间时常用）：
- **自爆只允许发生在白天发言阶段**：`_can_wolf_explode` 要求
  `phase in {sheriff_speech, sheriff_pk_speech, day_speech, day_pk_speech}`；
  `day_order`（警长安排顺序）不算，需要先用 `_apply(state, sheriff, {"action":"sheriff_order",...})` 走完那一拍。
- **女巫无药时仍会被问是否使用解药**：`night_witch_save` 那一拍不看 `witch_medicine`，
  但 `use=True` 且无药会抛错，所以这一拍只有"不救"一种合法回应，替引擎走完不改变博弈内容。

## 4. 桥接模式下的细节

- **AI 骑士决斗是同步调用**：引擎在每次公开发言之后立刻问骑士，等不了异步回写。
  要让它真动手，必须**提前**把 `{"action":"knight_decide","target":<座位|null>}` 写进
  `codex_bridge/responses/<task_id>.json`：
  `task_id = sha256(f"{game_id}|{day}|{phase}|{seat}|knight_decide|{sequence}")[:24]`，
  `sequence = state["action_seq"] + 1`（`_apply` 不改 `action_seq`，发言被应用时就地取值）。
  没预写 → 返回 None → 这一拍不决斗（只留孤儿任务文件，无副作用）。**每提交一次发言就要重新预写一次。**
- 跨 provider 续局没问题：provider 是启动参数、不进存档；`--state-file` 指副本验证更稳。
- 判断"是否推进"别看 phase（`day_speech` 要连过 12 人），看 `len(history)` 或 `pending.sequence`。
- 真人出局后整局都由 AI 驱动，直到赛后复盘/MVP 票选才会重新轮到真人。

## 5. 自动推进（不要我参与）

1. 页面标题栏 `▶ 自动推进` + 间隔下拉：`scheduleBridgePoll()` 里 `can_human_act` 时**先 return**，
   再走 `waiting_external` / MVP 分支，最后才是 `autoAdvance`；设置存 localStorage。真人回合不受影响。
2. `tools/auto_advance.py`：循环 POST `{"action":"continue"}`。**必须给面板固定端口**（`--port 0` 是随机的）。
3. `启动狼人杀.bat` = `panel_game.py --port 0 --open-browser`（provider 取自存档里的 `provider_config`）。
   面板可能被重复双击起多个实例，它们共用同一个 state 文件——并发写有风险，
   排查/维护前先用 netstat 扫端口 + `/api/health` 摸清有几个在跑。

## 6. Provider 对照与约定

| 取值 | 是什么 | rain 的叫法 |
| --- | --- | --- |
| `codex-cli` | 本机 Codex CLI，吃 ChatGPT 账号额度 | "和 codex 玩"（他一直在玩的） |
| `cli` | 通用 CLI 容器（需本机装 ollama/claude/gemini） | 我口中的"cli 模式" |
| `codex` | 文件桥接，等外部 Worker 写 `responses/<id>.json` | "桥接模式"（他不想玩） |
| `api` | HTTP API Key（OpenAI 兼容） | "apikey 模式" |
| `builtin` | 内置离线 AI | "离线 AI" |

- 只有一个抽象方法 `request_action(visible_state, request) -> dict | None`；`None` = 等外部写回。
- 与后端无关的资产都在 `game/ai_providers.py` 模块级：`action_schema` / `action_prompt` /
  `validate_intent` / `parse_action_payload`。新增后端要同步 `create_provider` 与 `--provider` choices。
- 新后端名字**不要叫 `codex`**（`human_game.py` 用 provider 名字跳过 AI 骑士决斗）。
- `ProviderFailure` = 重试无用（key/额度/网络），立即抛；`ValueError` = 输出不合法，才重试。
- 面向用户的报错要过 `summarize_cli_error()` / `summarize_api_error()`。
- 铁律：**一次调用 = 一个座位的 `visible_state` = 一个独立进程**。
- Windows 用 `split_cli_args()` 切参数，别用 `shlex.split`。
- 改页面交互后怎么验证（本机没装 agent-browser，别假设能用）：抽出 `<script>`，用 Node 配 DOM 桩
  （`document`/`localStorage`/`location`/`fetch`/Proxy 假元素），把断言**追加到同一份脚本末尾**，
  这样能直接读写同一作用域的 `state` / `bridgeTimer` / `scheduleBridgePoll`。

## 7. 本机环境坑

- 改完 `game/` 或 `panel_game.py` **必须重启服务**（模块已在内存）。
  重启安全：`load_state()` 每请求读盘，不加 `--reset` 不清档。
- `panel_game.py` 默认端口 8765 被本机别的服务占（返回 404）；桥接模式固定 18765。
- bash 缺 coreutils（`ls`/`cat`/`head`/`tail`/`dirname` 不存在），`tasklist`/`netstat` 输出是 GBK。
- **本机有 HTTP 代理**（`HTTP_PROXY`），连 `127.0.0.1` 也会被拦；测本地端点先设 `no_proxy=127.0.0.1,localhost`。
- bash 没有 `timeout`（Windows 的 `TIMEOUT.EXE` 是暂停命令）；要限时用 Python `subprocess.run(timeout=N)`。
- 起 `ThreadingHTTPServer` 测试用 `poll_interval=0.02`，否则 `shutdown()` 白等 0.5 秒。
- 单次子进程启动约 0.35s（python）/ 0.52s（.cmd），端到端测试别驱动太多真 AI 行动。
- `--cli-args` 的值以 `-` 开头时要用 `--cli-args=--xxx` 形式。
- `run_in_background` 起的服务能跨对话轮次存活。

## 8. 已知未修（动之前先问）

- 警长票被 `_remember(..., "vote", ...)` 记成"怀疑"（`beliefs.suspects` 方向反了）。修法：`_remember`
  加 `counts_as_suspicion` 参数，`sheriff_vote` 只记 `votes`。中途改会立刻改变 AI 行为，等局终再动。
- 内置 AI 隐狼学到魔镜少女/预言家后，**发言里不会引用自己的查验结果**（`ai_providers._speech` 只认
  `seer_checks` / `mirror_peeks`），`_target_for` 也不会跳过已查验目标。引擎规则是对的，只是话术缺失。
