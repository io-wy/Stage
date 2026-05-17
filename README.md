# 戏台 (Xitai) — 多 Agent 编排引擎

Director 统筹全局，Agent 各尽其能。

## 架构

```
导演 (Director) — ReAct 循环，观察 StateBoard，调度 Agent
戏子 (Agent) — CoreCoder ReAct 循环，执行具体任务
戏台 (StateBoard) — 全局状态面板，唯一真相源
```

## 两种戏子模式

- **一次性**：`spawn_agent` → 执行 → 返回 → 销毁
- **常驻**：`ResidentAgent` 常驻内存，持久 transcript，可交互

## 快速开始

```bash
uv sync
cp .env.example .env  # 编辑填入 LLM key/base/model
python run.py "你的目标"
```

或分阶段执行：
```bash
python phased_run.py plan "目标" -o plan.json
python phased_run.py step plan.json
```

## Fallback 机制

三层兜底：
1. `spawn_agent` 自动 retry 瞬时错误（timeout/connection/429，3次指数退避）
2. 导演弹性决策：观察状态 → `replan` / `spawn_resident` / `ask_human`
3. 3次失败强制 `ask_human`

## Agent 类型

| Agent | 职责 | 工具 |
|-------|------|------|
| coder | 写代码 | read/write/edit/glob/grep/bash |
| reviewer | 代码审查 | read/write/edit/glob/grep/bash |
| tester | 写和跑测试 | read/write/edit/bash |
| researcher | 搜索研究 | web_search/read/bash/glob/grep |
| monitor | 系统验证 | read/bash |

## 测试

```bash
uv run pytest tests/ -v
```

## 配置

编辑 `.env`：
```
LLM_API_KEY=sk-xxx
LLM_API_BASE=https://api.example.com/v1
LLM_MODEL=gpt-4
```
