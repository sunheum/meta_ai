from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised in minimal test runtime
    END = "__end__"
    START = "__start__"
    StateGraph = None


class ReviewGraphState(TypedDict, total=False):
    review_round: int
    max_review_rounds: int
    pending_count: int
    payload: dict[str, Any]


ReviewNode = Callable[
    [ReviewGraphState],
    Awaitable[ReviewGraphState],
]


class _ManualCompiledGraph:
    def __init__(self, review_node: ReviewNode) -> None:
        self._review_node = review_node

    async def ainvoke(
        self,
        state: ReviewGraphState,
    ) -> ReviewGraphState:
        current = state
        while (
            current.get("pending_count", 0) > 0
            and current.get("review_round", 0)
            < current.get("max_review_rounds", 0)
        ):
            current = await self._review_node(current)
        return current


def build_review_graph(review_node: ReviewNode):
    if StateGraph is None:
        return _ManualCompiledGraph(review_node)
    builder = StateGraph(ReviewGraphState)
    builder.add_node("review", review_node)
    builder.add_edge(START, "review")

    def route(state: ReviewGraphState):
        if (
            state.get("pending_count", 0) > 0
            and state.get("review_round", 0)
            < state.get("max_review_rounds", 0)
        ):
            return "review"
        return END

    builder.add_conditional_edges("review", route, ["review", END])
    return builder.compile()

