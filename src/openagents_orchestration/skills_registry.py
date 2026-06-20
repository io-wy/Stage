"""SkillRegistry — discover methodology skill packages for progressive disclosure.

戏台的 ``skills/`` 下每个子目录（含 ``SKILL.md``）是一个**方法论 skill**——
read-and-follow 的 playbook（对抗审查 / 影响扫描 / pre-verify / brainstorming），
内容同源自 ``.claude/skills/*.md``（见 ``skills/README.md``）。本模块实现 skill
「渐进披露」的两级：

- **L1 catalog**：每个 skill 的 name + description，由 runner 注入戏子 system
  prompt（见 ``runner._run_single``），让戏子"知道有哪些 skill"。
- **L2 doc**：单个 skill 的 SKILL.md 全文，由 ``read_skill`` 工具按需取回，戏子
  照着做（无 L3 执行层——方法论不是可执行 pipeline）。

SKILL.md 的 YAML frontmatter 提供 name/description。解析**容错**：缺 frontmatter
或格式错误的目录会回退用目录名、跳过坏内容，不影响其余 skill。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SkillMeta:
    """A discovered skill's catalog metadata."""

    name: str
    description: str
    path: Path  # path to the skill's SKILL.md


def _default_skills_dir() -> Path:
    """Locate the project's ``skills/`` directory.

    Prefer ``skills/`` relative to cwd. Fall back to the path relative to this
    file so the registry still resolves when an agent has chdir'd into a work_dir.
    """
    cwd_skills = Path("skills")
    if cwd_skills.is_dir():
        return cwd_skills
    return Path(__file__).resolve().parent.parent.parent / "skills"


class SkillRegistry:
    """Discover skill packages under ``skills/`` and expose catalog + docs."""

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        self._skills_dir = (
            Path(skills_dir) if skills_dir is not None else _default_skills_dir()
        )
        self._cache: list[SkillMeta] | None = None

    def discover(self, *, refresh: bool = False) -> list[SkillMeta]:
        """Scan ``skills/*/SKILL.md`` and parse frontmatter. Cached after first call."""
        if self._cache is not None and not refresh:
            return self._cache
        metas: list[SkillMeta] = []
        if self._skills_dir.is_dir():
            for child in sorted(self._skills_dir.iterdir()):
                skill_md = child / "SKILL.md"
                if child.is_dir() and skill_md.is_file():
                    meta = self._parse(skill_md)
                    if meta is not None:
                        metas.append(meta)
        self._cache = metas
        return metas

    @staticmethod
    def _parse(skill_md: Path) -> SkillMeta | None:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return None
        front = SkillRegistry._extract_frontmatter(text)
        name = ""
        description = ""
        if front is not None:
            name = str(front.get("name", "") or "").strip()
            description = str(front.get("description", "") or "").strip()
        # Fall back to the directory name so a skill with malformed/missing
        # frontmatter is still addressable (it can at least be run by dir name).
        if not name:
            name = skill_md.parent.name
        if not name:
            return None
        return SkillMeta(name=name, description=description, path=skill_md)

    @staticmethod
    def _extract_frontmatter(text: str) -> dict | None:
        """Parse the leading ``---``-delimited YAML block, if any."""
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return None
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return None
        try:
            data = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError:
            return None
        return data if isinstance(data, dict) else None

    def render_catalog(self) -> str:
        """L1 fragment: a compact list of every skill's name + description."""
        metas = self.discover()
        if not metas:
            return ""
        lines = [
            "# Available methodology skills",
            "These are read-and-follow playbooks, not executable tools. "
            "Read one in full with `read_skill(skill_name)`, then follow its steps yourself.",
            "",
        ]
        for meta in metas:
            lines.append(f"- {meta.name}: {meta.description or '(no description)'}")
        return "\n".join(lines)

    def get_doc(self, name: str) -> str | None:
        """L2: full SKILL.md text for a named skill, or None if not found."""
        for meta in self.discover():
            if meta.name == name:
                try:
                    return meta.path.read_text(encoding="utf-8")
                except OSError:
                    return None
        # Also accept lookup by directory name.
        candidate = self._skills_dir / name / "SKILL.md"
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def names(self) -> list[str]:
        """All discovered skill names (sorted by directory)."""
        return [meta.name for meta in self.discover()]
