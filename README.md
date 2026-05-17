# 戏台 (Xitai) — 多 Agent 编排引擎

Director 统筹全局，Agent 各尽其能。

## 架构

```
导演 (Director) — ReAct 循环，观察 StateBoard，调度 Agent
戏子 (Agent) — CoreCoder ReAct 循环，执行具体任务
戏台 (StateBoard) — 全局状态面板，唯一真相源
```

## 亮点

waiting for eplain (or you can read the repo)
- For single Agent: Context, Tool, Pattern, Skill
- For multi Agent: Fallback, Communication, Duty, SubAgent

## 快速开始

```bash
uv sync
cp .env.example .env  # 编辑填入 LLM key/base/model
python run.py "objective"
```

或分阶段执行：
```bash
python phased_run.py plan "objective" -o plan.json
python phased_run.py step plan.json
```

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
