"""轻量 route classifier — 问题 → 知识空间 tag。

先做规则版,保持可解释 / 可评测。后续 query rewrite 只能消费这里维护的受控 alias,
不直接读取答案正文。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    selected_route: str = "general"
    confidence: float = 0.0
    candidates: list[str] = Field(default_factory=list)
    filter_tags: list[str] = Field(default_factory=list)
    matched_aliases: list[str] = Field(default_factory=list)
    reason: str = ""


_ROUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "SAST 设施指南": (
        "服务器",
        "nas",
        "打印机",
        "云打印",
        "内网",
        "网络",
        "tailscale",
        "部署",
        "自托管",
        "gitlab",
        "overleaf",
        "jellyfin",
        "ups",
        "101服务器",
        "反代",
    ),
    "SAST 规章制度": (
        "规章",
        "制度",
        "守则",
        "规则",
        "商业行为",
        "冰箱",
        "冰柜",
    ),
    "SAST Link": (
        "sast link",
        "link账号",
        "oauth",
        "profile",
        "统一身份认证",
    ),
    "SAST FreshCup": (
        "freshcup",
        "新柚杯",
        "比赛管理系统",
    ),
    "SAST Evento": (
        "evento",
        "活动辅助",
        "活动反馈",
    ),
    "SAST 说明书 Public 版": (
        "部门",
        "软件研发部",
        "多媒体部",
        "电子部",
        "办公室",
        "外联部",
        "科宣部",
        "赛事部",
        "招新",
        "有哪些组",
    ),
}


class RouteClassifier:
    """基于受控 alias 的 route classifier。"""

    def __init__(self, aliases: dict[str, tuple[str, ...]] | None = None):
        self._aliases = aliases or _ROUTE_ALIASES

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
