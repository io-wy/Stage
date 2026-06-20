"""Tests for SkillRegistry — discovery and progressive-disclosure catalog."""

from __future__ import annotations

from pathlib import Path

from openagents_orchestration.skills_registry import SkillRegistry


def _write_skill(
    skills_dir: Path, name: str, *, description: str = "", body: str = "Body."
) -> None:
    d = skills_dir / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


def test_discover_finds_all_skills(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "alpha-pipeline", description="Does alpha.")
    _write_skill(skills, "beta-pipeline", description="Does beta.")
    reg = SkillRegistry(skills_dir=skills)
    metas = reg.discover()
    assert {m.name for m in metas} == {"alpha-pipeline", "beta-pipeline"}
    assert reg.names() == ["alpha-pipeline", "beta-pipeline"]  # sorted by dir


def test_discover_is_cached(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "alpha-pipeline", description="a")
    reg = SkillRegistry(skills_dir=skills)
    first = reg.discover()
    _write_skill(skills, "beta-pipeline", description="b")  # added after cache
    assert reg.discover() == first  # cached, beta not seen
    assert len(reg.discover(refresh=True)) == 2  # refresh picks it up


def test_render_catalog_includes_name_and_description(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "code-review-pipeline", description="Static code review.")
    catalog = SkillRegistry(skills_dir=skills).render_catalog()
    assert "code-review-pipeline" in catalog
    assert "Static code review." in catalog
    assert "read_skill" in catalog


def test_get_doc_returns_full_skill_md(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "x-pipeline", description="d", body="UNIQUE_BODY_MARKER")
    reg = SkillRegistry(skills_dir=skills)
    doc = reg.get_doc("x-pipeline")
    assert doc is not None and "UNIQUE_BODY_MARKER" in doc
    assert reg.get_doc("nonexistent") is None


def test_malformed_frontmatter_falls_back_to_dirname(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    bad = skills / "broken-pipeline"
    bad.mkdir()
    (bad / "SKILL.md").write_text("# Heading, no frontmatter\n", encoding="utf-8")
    metas = SkillRegistry(skills_dir=skills).discover()
    assert len(metas) == 1
    assert metas[0].name == "broken-pipeline"  # fallback to dir name
    assert metas[0].description == ""


def test_empty_or_missing_dir_returns_empty(tmp_path):
    reg = SkillRegistry(skills_dir=tmp_path / "nonexistent")
    assert reg.discover() == []
    assert reg.render_catalog() == ""


def test_dir_without_skill_md_is_skipped(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "not-a-skill").mkdir()  # no SKILL.md
    _write_skill(skills, "real-pipeline", description="real")
    reg = SkillRegistry(skills_dir=skills)
    assert reg.names() == ["real-pipeline"]


def test_real_project_skills_are_discoverable():
    """Smoke test against the repo's actual skills/ directory (methodology skills)."""
    reg = SkillRegistry()  # default dir resolution
    names = reg.names()
    # the curated methodology playbooks copied from .claude/skills/
    assert "adversarial-review" in names
    assert "change-impact-scan" in names
    assert "pre-verify" in names
    assert "brainstorming" in names
    # pitfall-journal is deliberately NOT exposed to ephemeral agents
    assert "pitfall-journal" not in names
    # every real skill should carry a description
    metas = {m.name: m for m in reg.discover()}
    assert metas["adversarial-review"].description
