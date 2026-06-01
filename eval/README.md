# 戏台 (Xitai) 评估套件

三个评估方向，覆盖不同能力维度.

---

## 套件

### 1. SWE-bench-lite — 软件工程任务

评估戏台修复真实 GitHub issue 的能力.

```bash
# 跑前 3 题 (推荐，资源占用大)
uv run python -m eval.main --suite swe_bench_lite --limit 3

# 指定本地数据
uv run python -m eval.main --suite swe_bench_lite --source data/swe-lite.jsonl --limit 5
```

**流程**: 加载 issue → clone 代码库 → 导演调度 coder/reviewer/tester → 生成 patch → 执行测试验证

**指标**: patch 是否 apply 成功 + 测试是否通过

**依赖**: `datasets` (HuggingFace), `git`

---

### 2. HumanEval — 代码生成

评估戏台解决标准编程问题的能力.

```bash
# 跑前 10 题
uv run python -m eval.main --suite humaneval --limit 10

# 跑全部 164 题
uv run python -m eval.main --suite humaneval
```

**流程**: 加载问题 → 写入 skeleton 代码 → 导演调度实现 → 执行 canonical tests

**指标**: 测试通过率

**依赖**: `datasets` (HuggingFace)

---

### 3. Custom — 自建评估

从 YAML 文件加载自定义任务，专门测试多 Agent 编排能力.

```bash
# 跑全部自建任务
uv run python -m eval.main --suite custom

# 只跑 easy 难度
uv run python -m eval.main --suite custom --difficulty easy

# 只跑前 5 个
uv run python -m eval.main --suite custom --limit 5
```

**流程**: 解析 YAML → 初始化文件系统 → 导演调度 → 执行验证规则

**指标**: 验证通过率 + 调度质量 + 状态一致性 + 资源效率

**任务定义**: 见 `eval/custom/tasks/*.yaml`

---

## 目录结构

```
eval/
  __init__.py
  base.py              # EvalTask / EvalResult / EvalHarness 基类
  main.py              # CLI 入口
  README.md
  swe_bench_lite/
    __init__.py
    loader.py          # 加载 SWE-bench 数据
    harness.py         # 运行 + 验证
    verify.py          # patch apply + test
  humaneval/
    __init__.py
    loader.py          # 加载 HumanEval 数据
    harness.py         # 运行 + 验证
    verify.py          # 测试执行
  custom/
    __init__.py
    harness.py         # YAML 任务运行器
    tasks/
      coding_easy_001.yaml
      coding_medium_001.yaml
      reasoning_001.yaml
```

---

## 添加新评估

1. 继承 `eval.base.EvalHarness`
2. 实现 `load_tasks()` 和 `run_task()`
3. 注册到 `eval/main.py` 的 `SUITES` 字典
4. 运行: `uv run python -m eval.main --suite your_suite`

---

## 缓存

外部数据集自动缓存到 `.eval_cache/`:
- `.eval_cache/swe_bench_lite/test.jsonl`
- `.eval_cache/humaneval/test.jsonl`

删除缓存重新下载: `rm -rf .eval_cache/`

---

## 报告格式

```json
{
  "harness": "custom",
  "summary": {
    "total": 3,
    "passed": 2,
    "pass_rate": 0.667,
    "avg_success_score": 0.78,
    "avg_steps": 8.3,
    "avg_tokens": 12500,
    "avg_duration_sec": 45.2
  },
  "by_difficulty": {
    "easy": {"count": 1, "passed": 1, "pass_rate": 1.0, "avg_success_score": 1.0},
    "medium": {"count": 2, "passed": 1, "pass_rate": 0.5, "avg_success_score": 0.67}
  },
  "tasks": [
    {"task_id": "coding-easy-001", "success": true, "success_score": 1.0, ...}
  ]
}
```
