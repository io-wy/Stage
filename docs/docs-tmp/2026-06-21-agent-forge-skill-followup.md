---
title: Agent Forge Skill — Follow-up
status: pending
created: 2026-06-21
---

# Agent Forge Skill（方法论 skill）

## 背景

`spawn_agent` 和 `sub_agent` 现在支持 `agent_spec` 参数：
- 可以在调用时 inline 定义一次性角色
- 以 `agents/_base.json` 为 base，用 `+tool`/`-tool` 增减工具
- 用 `prompts.roles.<role>:ROLE` 等引用 prompt 积木

但这给 agent 带来了新的决策负担：**什么时候该造一个 sub-agent / spawn 一个临时角色？怎么造？造完后怎么协作？**

## 目标

新增一个运行时方法论 skill（建议命名 `agent-forge`），教戏子：

1. **什么时候需要造 sub-agent / 临时角色**
   - 任务需要与父任务隔离上下文窗口（长调研、独立审计）
   - 现有 `agents/` 预定义角色的工具集不匹配（如只需要读文件 + grep 的安全审查）
   - 需要并行探索多个独立方向
   - 需要独立验证/复核，避免父 agent 的自我确认偏见

2. **怎么造（agent_spec 字段）**
   - `id`: 唯一标识这次性角色，如 `"security-auditor-t1"`
   - `prompts`: 引用 `prompts.roles.<role>:ROLE` + `prompts.constraints:*`
   - `tools`: 基于 `_base.json` 用 `+tool`/`-tool` 精确裁剪
   - `pattern.config.max_steps`: 限制步数，防止子任务失控

3. **spawn_agent vs sub_agent 怎么选**
   - `spawn_agent(task_id=..., agent_spec=...)`: 执行 StateBoard 上的任务，带产物验证、retry、失败标记
   - `sub_agent(agent_spec=..., instruction=...)`: 轻量委派，拿总结返回，不验证产物，适合探索/调研

4. **通信与协作规则**
   - 子 agent 有 `check_messages` / `send_message`，可与其他 agent 通信
   - 父 agent 应通过 `send_message` 给子 agent 发送补充上下文或终止信号
   - 子 agent 完成前调用 `complete_task`（coder）或返回总结（sub_agent）
   - Director 通过 `show_state` 观察子 agent 状态，决定干预/重试/收尾

5. **常见反模式**
   - 为小于 3 次文件读写的任务造 sub-agent（ overhead 过高）
   - agent_spec 里列过多工具（失去角色聚焦意义）
   - 子 agent 嵌套超过 2 层（`__sub_agent_depth__` 限制）
   - 用临时角色做需要长期状态/ resident 的任务

## 实现步骤

1. 在 `.claude/skills/agent-forge.md` 编写方法论（唯一信源）
2. 复制到 `skills/agent-forge/SKILL.md`（运行时 catalog）
3. 更新 `skills/README.md` 的同步表格
4. 验证 `SkillRegistry` 能扫描到，且 `read_skill("agent-forge")` 能取到全文
5. 可选：在 `agents/coder.json` / `agents/director.json` 默认工具里保留 `read_skill`，让 agent 能自学

## 依赖

- 已完成：`spawn_agent` 的 `agent_spec` 支持
- 已完成：`sub_agent` 的 `agent_spec` 支持
- 已完成：runner 简化（无动态注册，工具自己编译）
