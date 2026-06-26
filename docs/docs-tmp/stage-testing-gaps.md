# Stage 测试缺口与补齐计划

> 记录时间：2026-06-20
> 背景：已清理 54 个低价值测试，保留 10 个核心行为测试。本文档列出当前最缺的测试类型及可执行方案。

---

## 当前保留测试清单

| 文件 | 覆盖范围 |
|------|---------|
| `test_corecoder_enhanced.py` | CoreCoderPattern ReAct loop（tool 并行、失败恢复、plan mode、verification） |
| `test_corecoder_planning.py` | planning phase + clarification 阈值 |
| `test_run_corecoder.py` | 单 agent 恢复闭环 |
| `test_resident.py` | 常驻 agent 生命周期、idle 超时、消息积压 |
| `test_integration.py` | Director 工具链与 StateBoard 集成（仍 mock runner_delegate） |
| `test_state_board.py` | 核心状态机 + artifact 两阶段验证 |
| `test_task_state_machine.py` | 任务状态转换契约 |
| `test_decision_history.py` | Director 决策历史 |
| `test_collaboration.py` | 协作模式开关 + 信号解析 |
| `test_collaboration_state_machine.py` | coder/reviewer 协议状态机 |

**当前基线**：`uv run pytest tests/ -q` → 116 passed。

---

## 缺口 1：编排黄金路径测试（最缺）

### 现状问题
`test_integration.py` 里的 `test_spawn_success_updates_board` 把 `runner_delegate` mock 成返回字符串：

```python
mock_delegate = AsyncMock(return_value="Completed.\n\nFILES_CREATED: hello.py")
```

这意味着 **Director → spawn_agent → 真正跑 coder → 产出文件 → 验证 artifact** 这条交付主线从未在测试里真正跑通过。

### 应该测什么
用 `FakeLLMClient` 分别驱动 Director 和 coder，让 `OrchestratorRunner.run()` 真实执行，不 mock runner_delegate。

### 最小场景
```
objective: "修复 tests/test_state_board.py 里的一个 bug"
  → Director show_state
  → Director spawn_agent(coder, t1)
  → coder 跑 CoreCoderPattern（FakeLLM 返回 read→edit→bash pytest）
  → coder 发信号 REVIEW_READY
  → Director show_state 看到完成
  → Director finalize
```

### 断言点
- `board.get_task("t1").status == TaskStatus.COMPLETED`
- `board.artifacts["tests/test_state_board.py"].status == "verified"`
- `board._final_summary` 非空
- 真实文件被修改且测试通过
- `DecisionHistory` 中记录了 spawn_agent → completed

### 实现要点
- 构造 `FakeLLMClient` 时区分 agent_id：Director 的 LLM 只返回 `show_state` / `spawn_agent` / `finalize`；coder 的 LLM 返回 `read_file` / `edit_file` / `bash`。
- 使用 `tmp_path` 作为工作目录，避免污染真实文件。
- 可能需要在 `agent.json` 外构造一个最小测试配置，减少无关工具干扰。

---

## 缺口 2：失败恢复路径测试

### 现状问题
`test_corecoder_enhanced.py` 测了 Pattern 层的连续失败诊断提示，但 **没测 Director 是否据此做出决策**（retry / replan / ask_human / finalize）。

### 应该覆盖的场景

#### 2.1 LLM 返回不存在的工具名
- **输入**：coder LLM 返回 `tool_calls=[{"name": "nonexistent_tool"}]`
- **期望**：CoreCoderPattern 把错误喂回 LLM，不崩溃；transcript 中出现 tool_error；最终可能失败或重试。
- **断言**：`result.stop_reason` 为失败；`ctx.state["__consecutive_tool_failures__"]` 增加。

#### 2.2 Tool 连续失败 3 次触发 replan
- **输入**：coder 连续调用 `edit_file` / `bash` 失败 3 次。
- **期望**：Pattern 层触发诊断；Director 层看到任务失败后调用 `replan`。
- **断言**：
  - `board.get_task("t1").status == TaskStatus.FAILED` 或出现子任务 `t1a` / `t1b`
  - `DecisionHistory` 中有 `replan` 决策
  - `board.events` 中有 `task.replan` 事件

#### 2.3 Coder 跑了 30 步没产出
- **输入**：coder LLM 反复返回无意义 tool 调用，不产 artifact。
- **期望**：Director 的 `suggest_fallback` 给出 `ask_human` 或 `spawn resident` 建议；Director 最终调用 `ask_human`。
- **断言**：`board.get_agent("coder-t1").steps_used >= 30`；`board._human_channel` 中有待回答问题。

#### 2.4 依赖任务失败，下游任务 blocked
- **输入**：t1 失败，t2 依赖 t1。
- **期望**：t2 不会被调度；`board.tasks_blocked()` 包含 t2；Director 不会 spawn_agent(t2)。
- **断言**：`board.tasks_ready()` 为空；如果 Director 错误尝试 spawn t2，工具层应抛异常。

---

## 缺口 3：工具真实副作用测试

### 现状问题
`write_file`、`edit_file`、`bash_tool` 几乎没被真实测过。交付平台如果工具本身不可靠，上层编排再完美也白搭。

### 应该覆盖的契约

#### 3.1 `write_file`
- 写完后文件存在、内容完全匹配。
- 覆盖已存在文件时，原内容被替换。
- 路径不存在时自动创建父目录。
- 写入空内容时文件存在且为空。

#### 3.2 `edit_file`
- `old_string` 匹配成功时，文件内容被替换。
- `old_string` 不匹配时，**不修改原文件**，返回可识别的错误。
- 多行替换、边界空白处理正确。
- 对二进制文件或非 UTF-8 文件给出合理错误。

#### 3.3 `bash_tool`
- allowlist/blocklist 真的生效（如 `rm -rf /` 被拒绝）。
- 超时机制生效（长时间命令被杀掉）。
- 退出码、stdout、stderr 正确返回。
- 工作目录正确（相对路径基于 cwd）。

### 实现要点
- 用 `tmp_path` 作为沙盒目录。
- 这些测试应该调用真实工具，不 mock `Path.write_text` 或 `subprocess`。
- 对 `bash_tool` 的破坏性命令，使用显式 denylist 配置，确保测试本身安全。

---

## 缺口 4：Agent 间通信契约测试

### 现状问题
`test_integration.py` 测了 `send→claim→ack`，但这些都是直接调用 StateBoard 方法，没有测 **agent 在 ReAct loop 中自发发送和接收消息**。

### 应该覆盖的场景

#### 4.1 Coder 在 loop 里发消息给 reviewer
- **输入**：coder LLM 返回 `tool_calls=[{"name": "send_message", "arguments": {"to_agent": "reviewer-t1", "message": "TASK_REVIEW_READY[t1]: tests passed = 3"}}]`
- **期望**：消息进入 reviewer 的 mailbox，格式为结构化 SIGNAL。
- **断言**：`await board.claim_messages("reviewer-t1")` 拿到 signal，payload 中 `signal == "review_ready"`。

#### 4.2 Reviewer 在 loop 里收消息
- **输入**：先给 reviewer mailbox 发送 `REVIEW_READY` 信号，再让 reviewer LLM 调用 `check_messages`。
- **期望**：`check_messages` 返回消息内容，reviewer 据此决定 `APPROVED` 或 `FIX_NEEDED`。
- **断言**：reviewer 最终调用 `send_message` 回复 coder。

#### 4.3 消息格式错误
- **输入**：coder 发送格式错误的协作消息，如 `TASK_REVIEW_READY[`，缺少 task_id。
- **期望**：`parse_collaboration_message` 返回 None 或 fallback；系统不崩溃。
- **断言**：错误被记录到 `board.events`。

#### 4.4 广播风暴防护
- **输入**：某个 agent 向 `*` 广播大量消息。
- **期望**：所有注册 agent 收到，但系统不会无限循环或内存爆炸。
- **断言**：每个 recipient 只收到一次；`mailbox` 大小可控。

---

## 缺口 5：配置-运行时一致性测试

### 现状问题
`agent.json` 有 7 个 agent 角色，但旧文档和记忆里还是 4 个。配置漂移会在运行时以奇怪方式暴露。

### 应该测什么

#### 5.1 Agent 角色与 Pattern 一致性
```python
def test_agent_json_roles_match_patterns():
    cfg = load_config("agent.json")
    for agent in cfg.agents:
        assert agent.pattern.impl.endswith("Pattern")
        # director/team_leader 必须是调度型 Pattern
        if agent.id in ("director", "team_leader"):
            assert "director" in agent.pattern.impl.lower() or "team_leader" in agent.pattern.impl.lower()
```

#### 5.2 工具引用存在性
```python
def test_agent_json_tools_are_importable():
    cfg = load_config("agent.json")
    for agent in cfg.agents:
        for tool in agent.tools:
            import_class(tool.impl)  # 确保不抛 ImportError
```

#### 5.3 角色与文档一致性
```python
def test_agent_json_matches_documented_roles():
    documented = {"director", "coder", "reviewer", "researcher", "github_agent", "monitor", "team_leader"}
    actual = {a.id for a in load_config("agent.json").agents}
    assert actual == documented
```

#### 5.4 环境变量占位符可解析
```python
def test_agent_json_env_placeholders_have_defaults():
    cfg = load_config("agent.json")
    # LLM_API_BASE / LLM_MODEL 等必须要么有默认值，要么在 CI 环境已设置
```

---

## 下一步建议

1. **先补缺口 1**：写一个会失败的编排黄金路径测试。这是交付能力的真实基线。
2. **并行补缺口 3 和缺口 5**：工具真实副作用测试和配置一致性测试最容易写，ROI 最高。
3. **再补缺口 2 和缺口 4**：失败恢复和通信契约需要更多 FakeLLM 编排，但依赖缺口 1 的脚手架。

---

## 文件位置

- 本文档：`docs/docs-tmp/stage-testing-gaps.md`
- 相关代码：`tests/`、`src/openagents_orchestration/core/runner.py`、`src/openagents_orchestration/core/state_board.py`、`agent.json`
