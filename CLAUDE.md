# 项目级 AI Coding 约束（项目宪法）

> 本文件只装**对 Claude Code 的通用编码与流程约束**——任何项目都适用，不含项目特定内容。
> 本项目的概念 / 架构 / 设计理据 / 项目特定编码铁律 → `docs/docs-proj/design-rationale.md`。
> 结构性信息（模块 / 文件位置 / 函数签名 / 运行时流程）→ 用 `codegraph` / `grep` / `ls` 实时拿，禁止固化进文档（必漂移过期）。

---

## 1. 编码约束（反例免疫格式：WRONG + CORRECT + Why，让 AI 看见边界）

### X-01：配置 / 密钥 / URL → env，禁硬编码

```python
# WRONG
api_base = "http://10.x.x.x:8000/v1"
# CORRECT
api_base = os.environ["API_BASE"]   # .env + 占位
```

Why: 密钥进代码 = 泄漏；硬编码 URL = 换环境就改码。

### X-02：资源必须配对释放

```python
# WRONG: 开了不关
conn = await pool.acquire()
# CORRECT: try/finally 或 async with
async with pool.acquire() as conn:
    ...
```

Why: 连接 / 句柄 / 后台任务泄漏 = 内存与句柄累积，长跑 OOM。配对：`open↔close`、`acquire↔release`、`start↔stop`。

### X-03：异常不吞

```python
# WRONG
try:
    await run()
except Exception:
    pass
# CORRECT: 记录 + 让上层可恢复
except Exception as e:
    logger.error(...); raise   # 或显式降级
```

Why: 静默失败 = 上层看不到，任务卡死、难排查。

### X-04：跨线程 / 异步边界要桥接

```python
# WRONG: 同步上下文里裸碰事件循环 / 跨线程共享可变状态
def sync_fn():
    loop.create_task(...)
# CORRECT
await asyncio.to_thread(sync_fn)   # 或锁 / 消息传递
```

Why: 裸碰 = RuntimeError 或数据竞争。

### X-05：依赖引入——标准库 > 已有依赖 > 新依赖

引入新依赖前自查：① stdlib 是否已有等价？② 已有依赖能否覆盖？③ 必须引入则评估活跃度 / 许可 / CVE，并告知。

### X-06：公共函数 / 新逻辑必须有测试，且 mock 外部依赖

```python
# WRONG: 测试打真实外部服务（LLM / 网络 / DB）
result = await real_api.call()
# CORRECT: mock，断言行为
fake.generate.return_value = canned
```

Why: 真实外部依赖慢 / 贵 / 不稳 / 不可复现。

### X-07：单次写入 ≤ 400 行 / 12KB

超限分批（先写前半，再 Edit 追加）。要求完整输出时可突破。

---

## 2. 流程阻塞约束

```
功能实现 → 单元测试 → 自审 / 对抗审查 → 提交
```

顺序不可跳 / 不可并行；功能未完成不开始测试；测试未过不标完成；审查未过不 commit。明确要求跳过时记录原因后执行。

## 3. 改不全预防

1. **调用点扫描**：改函数签名 / 接口 → 用工具搜所有调用点，列影响范围再改
2. **正反配对**：新增 open / acquire / start / 注册 → 确认有对应 close / release / stop / 注销
3. **测试同步**：改实现逻辑 → 检查对应测试是否要更新

执行：单文件随 commit 自检；多文件先列影响范围；接口变更走 Plan 模式。

## 4. 不确定性声明

查证链：用工具读项目代码 → 依赖文件 → Context7 → WebSearch，无法验证则标 `# TODO: verify` 并告知。
禁止：编造 API 签名、未读源码声称「已分析」、跳过实测用模式匹配硬推。
查证链走完仍无果，或无实测数据支撑性能 / 收敛断言 → 明说「我还有东西不知道」。

## 5. 多模型对抗审查

对接 `.claude/skills/adversarial-review.md`。触发任一：核心逻辑变更 / 改动 ≥ 5 文件 / ≥ 200 行 / 明确要求 / 产出质量可疑。
流程：主模型自审 → 跨模型独立审查 → 双方都报 = 高置信；单方报 = 标「需确认」→ 按 Critical / Major / Minor / Suggestion 输出合并报告。

## 6. 踩坑进化闭环（PIT）

对接 `.claude/skills/pitfall-journal.md`。触发：错误代码被纠正 / 漏边界致 bug / 用了不存在的 API / 改不全 / 不确定时编造。
闭环：记 PIT 条目 → 同类 ≥ 2 次提炼为新约束 → 验证有效 ≥ 2 次固化为独立 skill。

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
