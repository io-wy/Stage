"""Controlled query rewrite for retrieval.

The rewriter only consumes route metadata and controlled aliases. It must not
read retrieved passages or generated answers, so rewrite behavior stays
auditable and safe to evaluate as its own stage.
"""

from __future__ import annotations

from pydantic import BaseModel

from openagents_orchestration.rag.route import RouteDecision


class QueryRewriteDecision(BaseModel):
    original_query: str
    rewritten_query: str
    selected_route: str = "general"
    used_aliases: tuple[str, ...] = ()
    added_terms: tuple[str, ...] = ()
    reason: str = ""

    @property
    def changed(self) -> bool:
        return self.rewritten_query != self.original_query


class ControlledQueryRewriter:
    """Append controlled route metadata to improve lexical recall."""

    def rewrite(self, query: str, route: RouteDecision | None) -> QueryRewriteDecision:
        original = query.strip()
        if route is None or route.selected_route == "general":
            return QueryRewriteDecision(
                original_query=original,
                rewritten_query=original,
                reason="no route metadata available",
            )

        controlled_terms = [route.selected_route, *route.matched_aliases]
        added_terms = tuple(_dedupe_new_terms(original, controlled_terms))
        rewritten = original if not added_terms else f"{original} {' '.join(added_terms)}"
        return QueryRewriteDecision(
            original_query=original,
            rewritten_query=rewritten,
            selected_route=route.selected_route,
            used_aliases=tuple(route.matched_aliases),
            added_terms=added_terms,
            reason="appended controlled route and alias metadata",
        )


def _dedupe_new_terms(query: str, terms: list[str]) -> list[str]:
    lowered = query.lower()
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        normalized = term.strip()
        key = normalized.lower()
        if not normalized or key in seen or key in lowered:
            continue
        seen.add(key)
        result.append(normalized)
    return result
