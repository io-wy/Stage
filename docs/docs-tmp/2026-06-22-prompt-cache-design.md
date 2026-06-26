# Prompt Caching 设计（路线 B 选定·实施方案）

> 状态：**路线 B 选定**（DeepSeek /v1 自动 caching）。2026-06-22。
> 目标：降低每次 LLM 调用重发固定开销（tools 6217 + static system 2748 ≈ 8965 token/次）的**成本**。

## 0. 先说清：省成本，不省 token 数

cached 命中的 input token 计费约 1/10~1/50（DeepSeek V4-Flash：cache-hit $0.0028 vs miss $0.14/1M，~98% 降）。**token 总数不变**，5.7 万还是 5.7 万，省的是钱和首 token 延迟。

## 1. 路线 B：DeepSeek `/v1`（openai_compatible）自动 caching —— 已确认可行

DeepSeek 官方文档证实（api-docs.deepseek.com/guides/kv_cache）：
- **Context Caching on Disk 默认开、零代码零配置**（无 cache_control、无 TTL、无 cache key）。
- 走 **OpenAI 兼容端点** `https://api.deepseek.com`，`deepseek-v4-flash` 支持。
- **只匹配从 token 0 的连续前缀**；中间匹配不算；64 token 为单位；best-effort 不保证 100%。
- 命中字段：`usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。

## 2. 前缀稳定性 —— 已就绪（corecoder 早为 cache 铺路）

- `_build_tool_schemas`(corecoder.py:728)：`sorted(key=tool_id)`，注释明写 "deterministic ordering → stable cache fingerprints"。**tools 顺序稳定** ✓
- `compose_system_prompt` 用 `__DYNAMIC_BOUNDARY__` 分隔 static(role/principles，稳定) 与 dynamic(runtime: cwd/budget，每次变)，`_split_system_prompt` 拆成 `[static, dynamic]` 两条 system message，static 在 `messages[0]` ✓
- **含义**：DeepSeek 从 token 0 缓存，能命中到 **static system 末尾**（dynamic 一变就截断）。所以稳定可缓存的是 static system；`tools` 字段缓不缓存**待实测**（DeepSeek 文档只明说 system+user 前缀）。

## 3. 一处必要小改：让 Stage 能「看到」命中

`openai_compatible.py._normalize_usage`(124-127) 只读 OpenAI 的 `prompt_tokens_details.cached_tokens`，**读不到 DeepSeek 的顶层 `prompt_cache_hit_tokens`**。

- 后果：caching **实际在省**（DeepSeek 服务端自动，与代码无关），但 Stage 的 usage 统计会显示 cached=0、**看不到/没法验证命中**。
- 改法（一处 ~3 行）：`_normalize_usage` 补 `if "prompt_cache_hit_tokens" in raw: meta["cached_tokens"] = int(raw["prompt_cache_hit_tokens"] or 0)`。
- 改 .venv（Stage 立即生效）+ 源码同步。**不需重装整个 746 版**（只动 openai_compatible 这一处）。

## 4. 实施步骤 + 分工

| 步骤 | 谁 | 内容 |
|---|---|---|
| ① 切端点 | **io-wy**（含密钥） | .env：DeepSeek → `api.deepseek.com`(`/v1`) + `provider=openai_compatible` + `LLM_*` 指向它（model `deepseek-v4-flash`） |
| ② 补命中字段 | 我 | openai_compatible.py（.venv+源码）补读 `prompt_cache_hit_tokens` |
| ③ 验证 | 一起 | 跑两次相同 objective，看第二次 `prompt_cache_hit_tokens` > 0；hit token 量揭示缓存了 static-system-only 还是含 tools |

## 5. 风险/待实测

- `tools`(6217) 缓不缓存待实测（看 hit token 量）。若不缓存，可考虑把 tools 内容也纳入可缓存前缀，或接受只缓存 static system。
- dynamic system 在 static 后会截断缓存；若想缓存更多，未来可调 dynamic 位置（corecoder 改动，暂不做）。
- DeepSeek caching best-effort，命中率非 100%。

## 6. 路线 A（未选·备忘）

改 SDK AnthropicClient 补 cache_control + 切 anthropic provider + 确认 DeepSeek `/anthropic` 支持。改 SDK 重、依赖端点支持，已被 B 取代。
