# skills/ — 戏子运行时方法论 skill

本目录下每个子目录（含 `SKILL.md`）是一个**方法论 skill**：read-and-follow 的
playbook。戏子运行时通过 `read_skill(skill_name)` 取全文后**自行遵循**。由
`SkillRegistry`（`src/openagents_orchestration/skills_registry.py`）扫描
`skills/<dir>/SKILL.md` 发现，注入戏子 system prompt 的 L1 catalog。

> 历史：早期这里放的是 10 个可执行 `*-pipeline`（带 `src/<pkg>/entrypoint.py`），
> 由 `run_skill` 工具执行。方法论 skill 没有 entrypoint，是「读了照做」，故
> `run_skill` 已一并移除——没有可执行 skill 就不留执行器。

## ⚠️ 单一信源与同步规则（防 CLAUDE.md §6 文档漂移）

这 4 个 `SKILL.md` 是 **`.claude/skills/*.md` 的副本**（两套用途不同：`.claude/skills/`
是给 AI 的 L4 方法论，本目录是给戏子运行时的同一份内容）：

| 运行时（本目录，给戏子）              | 唯一信源（给 AI 的 L4）                 |
| ------------------------------------- | -------------------------------------- |
| `skills/adversarial-review/SKILL.md`  | `.claude/skills/adversarial-review.md` |
| `skills/change-impact-scan/SKILL.md`  | `.claude/skills/change-impact-scan.md` |
| `skills/pre-verify/SKILL.md`          | `.claude/skills/pre-verify.md`         |
| `skills/brainstorming/SKILL.md`       | `.claude/skills/brainstorming.md`      |

**改方法论只改 `.claude/skills/<name>.md`，然后重新同步本目录：**

```bash
for n in adversarial-review change-impact-scan pre-verify brainstorming; do
  cp ".claude/skills/$n.md" "skills/$n/SKILL.md"
done
```

**未收录** `pitfall-journal`：它是「跨会话进化」的元方法论，戏子生命周期是
spawn→run→die，记 PIT 无对象消费，故不进运行时 catalog。
