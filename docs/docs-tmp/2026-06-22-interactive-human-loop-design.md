# 交互式 human-in-the-loop 设计（挂起待实现）

> 状态：**挂起**。2026-06-22 探明链路 + 定方案，暂不实现（io-wy 决定先用具体 objective 绕过 ask_human）。
> 背景：director 对模糊 objective 会 `ask_human` 暂停，run.py 非交互、`auto_finalize` 退出 → 0 产出。

## 需求

让 director（或任意 agent）`ask_human` 提问时，能从终端 `input()` 读人的答案、喂回去继续跑，形成「问→答→续」的终端交互闭环。

## 现状链路（为什么现在不行）

1. **ask_human 只登记**：`tools/director/ask_human.py` 的 invoke 调 `board.ask_human(question)` 登记问题、返回 "Waiting for human reply"，**不读 stdin、不阻塞**。
2. **director 暂停**（两条路触发 AWAITING_HUMAN）：
   - planning 阶段 clarification：`corecoder.py:_request_clarification_and_pause`(~1899)，confidence 低 → 调 ask_human + `return AWAITING_HUMAN`，**在进 ReAct 循环之前**（所以 0 steps、trace 看不到）。
   - ReAct 循环里 director 主动调 ask_human 工具。
3. **run.py 不续**：director 返回 AWAITING_HUMAN → `runner.run` 见无 finalize → `_auto_finalize` 直接退出(runner.py:430)，进程结束，没机会答。
4. **talk.py 要进程活着**：往 `inbox.jsonl` 写消息，director 下次 check_messages 读 —— 但 director 已退出，没人读。
5. **resume.py**：从快照恢复 session，但「注入答案 + director 读到续」未打通。

## 人答抽象（已就绪，可复用）

- `HumanChannel`(projects/human_channel.py)：`ask()` 登记 / `answer(qid, answer)` 注入 / `get_pending_questions()` / `get_answered_questions()` / `get_question(qid)`。
- `HumanChannelService`(projects/human_channel_service.py)：`reply_human(qid, answer)` / `get_pending_questions(project_id)` / `get_human_questions(answered=...)`。
- `StateBoard` 适配方法：`board.ask_human()` / `board.reply_human(qid, answer)`(:426) / `board.get_human_questions(answered=...)`(:439)。
- director transcript 持久化：`runner._run_single` 末尾 `_sessions.save(session_id="session-director-root", messages=ctx.transcript)`，重入时 context_assembler 续历史。

## 推荐方案：驱动层交互循环（不改 pattern 核心）

入口（建议 phased_run.py 加 `--interactive`，或新入口）做循环：

```python
input_text = objective
while True:
    outcome = await runner.run_agent("director", input_text, agent_id="director-root")
    pending = board.get_human_questions(answered=False)
    if not pending or board._final_summary:
        break
    q = pending[0]
    answer = input(f"\n[director 问] {q['question']}\n> ")        # 终端读
    board.reply_human(q["id"], answer)                            # 注入(标记已答)
    input_text = f"[你对问题 '{q['question']}' 的回答]: {answer}"  # 显式喂下一轮
```

要点：
- director 重入 `agent_id` 固定 `"director-root"` → session 同 → transcript 续（记得自己问过啥）。
- 答案**显式作为下一轮 input_text**，不依赖 snapshot 是否注入答案（最可控）。
- `board.reply_human` 标记已答 → pending 清空 → 循环退出条件成立。
- 多轮问答天然支持（while 循环）。

## 附带待办（顺带发现）

- **trace 盲区**：planning 阶段的 ask_human / LLM 调用走 `structured_generate` + `_request_clarification_and_pause`，**不触发 PATTERN_BEFORE_STEP / TOOL_BEFORE_INVOKE hook**，phased_run 的 printer 看不到。补救：给 planning/clarification 加 hook 或 printer。
- **planning token 不计入 budget**：planning 的 LLM 调用走 `structured_generate`（不经 `_invoke_llm`），token 没记进 board budget（真实跑见过 `Token: 0` 但确实调了 LLM）。

## 验证（实现后）

`python phased_run.py --interactive --real "Build a FastAPI app"` → director ask_human → 终端能答 → director 用答案继续 → 最终产出。
