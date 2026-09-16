# AIWolfGame 开发约定

开始工作前先阅读 `PROJECT_CONTEXT.md`、`README.md`，并检查 `git status` 与最近提交。

## 核心边界

- 项目同时保留原有全 AI 命令行模拟器和新的 1 真人 + 11 电脑网页游戏。
- 不要把完整法官状态或身份表传给 AI；所有 AI 决策必须从玩家可见状态生成。
- AI/Provider 只提交行动意图，技能合法性、投票、死亡、警徽和胜负均由确定性规则引擎裁决。
- 真人需要决定的阶段必须暂停，禁止自动替真人选择。
- 保存文件与 Codex 桥接任务不得包含真人或其他 AI 不应看到的额外视角。

## 工作方式

- 调查后再改动，保留现有可用功能，不顺手重构无关代码。
- 优先级：真人完整参与 → 角色规则 → 警长/PK/胜负 → 信息隔离 → Provider/桥接 → AI体验 → UI。
- 修改后至少运行 `python -m unittest discover -s tests -v`。
- 涉及网页时还要实际启动 `panel_game.py`，检查 `/api/health` 与首页可访问。
- 完成后更新 `PROJECT_CONTEXT.md`，记录已完成、未完成与下一步。
