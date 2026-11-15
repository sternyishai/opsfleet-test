"""NullTrioRetriever: the Golden Bucket is empty, and the graph runs unchanged (LLD 06)."""

from langchain_core.messages import AIMessage, ToolMessage

from agent.adapters.null import NullTrioRetriever
from agent.contracts.channel import AgentReply
from agent.domain.identity import BrandScope, Role, UserContext
from agent.domain.query import ResultSummary
from tests.fakes import FakeLLMFactory, ask, chat_service, fake_ports

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
SQL = "SELECT SUM(sale_price) AS revenue FROM order_items"


def test_null_retriever_returns_nothing() -> None:
    result = NullTrioRetriever().search("revenue by month", BrandScope(all_brands=True))

    assert result.matches == []
    assert result.dropped_below_threshold == 0


def _analyst_runs_sql_then_answers() -> FakeLLMFactory:
    return FakeLLMFactory(
        analyst=[
            AIMessage(
                content="", tool_calls=[{"name": "run_sql", "args": {"sql": SQL}, "id": "c1"}]
            ),
            AIMessage(content="Total revenue in 2025 was $1.5."),
        ]
    )


def test_a_turn_runs_retrieval_then_analysis_with_no_trios() -> None:
    ports = fake_ports(CEO, llm=_analyst_runs_sql_then_answers())
    service, graph = chat_service(ports)

    delivered, config = ask(service, "ceo", "What was total revenue in 2025?")

    [reply] = delivered
    assert isinstance(reply, AgentReply)
    assert reply.markdown == "Total revenue in 2025 was $1.5."
    assert reply.message_class is None
    state = graph.get_state(config).values
    assert state["trios"] == []
    assert state["retrieval_degraded"] is False
    assert state["ran_sql"] is True


def test_result_rows_never_enter_state() -> None:
    ports = fake_ports(CEO, llm=_analyst_runs_sql_then_answers())
    service, graph = chat_service(ports)

    _, config = ask(service, "ceo", "What was total revenue in 2025?")

    state = graph.get_state(config).values
    assert not [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert state["result_summaries"] == [
        ResultSummary(columns=["revenue"], row_count=1, key_figures={"revenue": "1.5"})
    ]


def test_a_follow_up_continues_the_conversation() -> None:
    llm = FakeLLMFactory(analyst=[AIMessage(content="It was $1."), AIMessage(content="$2.")])
    service, graph = chat_service(fake_ports(CEO, llm=llm))
    [first], config = ask(service, "ceo", "Revenue in 2025?")

    [second], _ = ask(service, "ceo", "And 2024?", conversation_id=first.conversation_id)

    assert second.conversation_id == first.conversation_id
    texts = [m.text for m in graph.get_state(config).values["messages"]]
    assert texts == ["Revenue in 2025?", "It was $1.", "And 2024?", "$2."]
