import pytest

from app.review_graph import build_review_graph


@pytest.mark.asyncio
async def test_review_graph_stops_at_round_limit():
    async def node(state):
        return {
            **state,
            "review_round": state["review_round"] + 1,
            "pending_count": 1,
        }

    graph = build_review_graph(node)
    result = await graph.ainvoke(
        {
            "review_round": 0,
            "max_review_rounds": 2,
            "pending_count": 1,
            "payload": {},
        }
    )

    assert result["review_round"] == 2

