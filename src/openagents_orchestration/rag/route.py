"""轻量 route classifier — 问题 → 知识空间 tag。

先做规则版,保持可解释 / 可评测。后续 query rewrite 只能消费这里维护的受控 alias,
不直接读取答案正文。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    selected_route: str = "general"
    confidence: float = 0.0
    candidates: list[str] = Field(default_factory=list)
    filter_tags: list[str] = Field(default_factory=list)
    matched_aliases: list[str] = Field(default_factory=list)
    reason: str = ""


_DEFAULT_ROUTE_ALIASES: dict[str, tuple[str, ...]] = {}


class RouteClassifier:
    """基于受控 alias 的 route classifier。"""

    def __init__(self, aliases: Mapping[str, Sequence[str]] | None = None):
        source = _DEFAULT_ROUTE_ALIASES if aliases is None else aliases
        self._aliases = {
            route: tuple(alias for alias in route_aliases if alias)
            for route, route_aliases in source.items()
        }

    def classify(self, query: str) -> RouteDecision:
        normalized = query.lower()
        scored: list[tuple[str, int, list[str]]] = []
        for route, aliases in self._aliases.items():
            matched = [alias for alias in aliases if alias.lower() in normalized]
            if matched:
                scored.append((route, len(matched), matched))
        if not scored:
            return RouteDecision(reason="no route alias matched")

        scored.sort(key=lambda item: item[1], reverse=True)
        selected, score, matched_aliases = scored[0]
        candidates = [route for route, _, _ in scored]
        total_alias_hits = sum(item[1] for item in scored)
        confidence = score / total_alias_hits if total_alias_hits else 0.0
        return RouteDecision(
            selected_route=selected,
            confidence=confidence,
            candidates=candidates,
            filter_tags=[selected],
            matched_aliases=matched_aliases,
            reason=f"matched aliases: {', '.join(matched_aliases)}",
        )
