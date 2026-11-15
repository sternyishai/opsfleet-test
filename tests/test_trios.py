"""NullTrioRetriever: the Golden Bucket is empty, and the graph runs unchanged (LLD 06)."""

from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage, ToolMessage

from agent.adapters.null import NullTrioRetriever
from agent.adapters.trios_sqlite import SqliteTrioRetriever
from agent.contracts.channel import AgentReply
from agent.contracts.tools import SearchTriosInput
from agent.domain.identity import BrandScope, Role, UserContext
from agent.domain.query import ResultSummary
from agent.domain.trios import TrioSearchResult
from agent.errors import TransientError
from agent.tools.analysis import search_golden_trios
from tests.fakes import (
    DIM,
    FakeEmbeddings,
    FakeLLMFactory,
    FakeTracer,
    analysis_decision,
    ask,
    chat_service,
    fake_ports,
    turn_context,
    unit,
)

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


# --- SqliteTrioRetriever over the seed Trios (slice 7) ----------------------------------------

SEED = Path("seed/trios")
MANAGER_A = UserContext(
    user_id="manager-a",
    role=Role.MANAGER,
    brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}),
    access_version=1,
)
MANAGER_B = UserContext(
    user_id="manager-b",
    role=Role.MANAGER,
    brands=frozenset({"Carhartt", "Columbia", "The North Face"}),
    access_version=1,
)
RETURNS = "which categories get returned most"  # a return-rate-by-category paraphrase
SEASONS = "when is our peak season"  # a seasonal-revenue paraphrase
CHURN = "Why did customer churn rise last month?"  # churn-monthly's question


def seed_vectors() -> dict[str, list[float]]:
    """Every question and paraphrase of the i-th seed Trio (by file name) on axis i, so a
    query equal to one of them scores 1 against that Trio and 0 against the others."""
    vectors: dict[str, list[float]] = {}
    for axis, path in enumerate(sorted(SEED.glob("*.yaml"))):
        trio = yaml.safe_load(path.read_text())
        for text in [trio["question"], *trio["paraphrases"]]:
            vectors[text] = unit(axis)
    return vectors


def retriever(
    tmp_path: Path, embeddings: FakeEmbeddings | None = None, *, min_score: float = 0.75
) -> SqliteTrioRetriever:
    return SqliteTrioRetriever(
        tmp_path / "app.db",
        seed=SEED,
        embeddings=embeddings or FakeEmbeddings(seed_vectors()),
        min_score=min_score,
    )


def refs(result: TrioSearchResult) -> list[str]:
    return [m.ref for m in result.matches]


def test_each_manager_gets_only_the_trio_of_their_own_brands(tmp_path: Path) -> None:
    trios = retriever(tmp_path)

    assert refs(trios.search(RETURNS, MANAGER_A.scope)) == ["return-rate-by-category@1"]
    assert refs(trios.search(SEASONS, MANAGER_A.scope)) == []
    assert refs(trios.search(SEASONS, MANAGER_B.scope)) == ["seasonal-revenue@1"]
    assert refs(trios.search(RETURNS, MANAGER_B.scope)) == []


def test_a_manager_never_gets_an_all_brands_trio(tmp_path: Path) -> None:
    result = retriever(tmp_path).search(CHURN, MANAGER_A.scope)

    assert result.matches == []
    # Only manager-a's own Trio was scored (and was too weak); the others were out of scope.
    assert result.dropped_below_threshold == 1


def test_the_ceo_gets_the_all_brands_trios(tmp_path: Path) -> None:
    trios = retriever(tmp_path)
    seed = {p.stem: yaml.safe_load(p.read_text()) for p in SEED.glob("*.yaml")}
    all_brands = sorted(k for k, t in seed.items() if t["scope"]["all_brands"])

    found = sorted(
        m.ref.removesuffix("@1")
        for name in all_brands
        for m in trios.search(seed[name]["question"], CEO.scope).matches
    )

    assert found == all_brands == ["category-revenue-change", "churn-monthly", "state-spend-gap"]


def test_a_match_carries_the_stored_method_not_figures(tmp_path: Path) -> None:
    [match] = retriever(tmp_path).search(RETURNS, MANAGER_A.scope).matches
    stored = yaml.safe_load((SEED / "return-rate-by-category.yaml").read_text())

    assert match.score == pytest.approx(1.0)
    assert match.question == stored["question"]
    assert match.sql == stored["sql"]
    assert match.method_summary == stored["method_summary"]
    assert [p.model_dump() for p in match.parameters] == stored["parameters"]
    assert match.tables_used == ["order_items", "products"]


def test_below_the_threshold_is_dropped_and_counted(tmp_path: Path) -> None:
    # Cosine 0.6 with the first seed Trio, 0 with the others.
    weak = [0.6 * a + 0.8 * b for a, b in zip(unit(0), unit(DIM - 2), strict=True)]
    vectors = seed_vectors() | {"something vaguely about revenue": weak}

    result = retriever(tmp_path, FakeEmbeddings(vectors)).search(
        "something vaguely about revenue", CEO.scope
    )

    assert result.matches == []
    assert result.dropped_below_threshold == 5  # every Trio in the CEO's scope was too weak


def test_the_best_paraphrase_counts_once_per_trio(tmp_path: Path) -> None:
    axis = sorted(p.stem for p in SEED.glob("*.yaml")).index("return-rate-by-category")
    side = DIM - 2

    def mix(on_axis: float, on_side: float) -> list[float]:
        return [on_axis * a + on_side * b for a, b in zip(unit(axis), unit(side), strict=True)]

    query = "returns by category over time"
    # 0.8 against the question and two paraphrases, 0.96 against the RETURNS paraphrase.
    vectors = seed_vectors() | {query: mix(0.8, 0.6), RETURNS: mix(0.6, 0.8)}

    result = retriever(tmp_path, FakeEmbeddings(vectors)).search(query, MANAGER_A.scope)

    [match] = result.matches
    assert match.ref == "return-rate-by-category@1"
    assert match.score == pytest.approx(0.96)


def test_the_limit_keeps_the_best_first(tmp_path: Path) -> None:
    trios = retriever(tmp_path, min_score=0.0)

    result = trios.search(CHURN, CEO.scope, limit=2)

    assert len(result.matches) == 2
    assert result.matches[0].ref == "churn-monthly@1"
    assert result.matches[0].score >= result.matches[1].score


def test_the_seed_is_embedded_once_then_only_queries(tmp_path: Path) -> None:
    embeddings = FakeEmbeddings(seed_vectors())
    retriever(tmp_path, embeddings).search(RETURNS, MANAGER_A.scope)
    retriever(tmp_path, embeddings).search(RETURNS, MANAGER_A.scope)  # a restart, same file

    assert embeddings.calls == [20, 1, 1]  # 5 questions + 15 paraphrases, then two queries


def test_an_embedding_outage_is_a_transient_trios_error(tmp_path: Path) -> None:
    down = FakeEmbeddings(seed_vectors(), fail=TransientError("busy", dependency="gemini"))

    with pytest.raises(TransientError) as raised:
        retriever(tmp_path, down).search(RETURNS, MANAGER_A.scope)

    assert raised.value.dependency == "trios"


# --- search_golden_trios and retrieve_trios (slice 7) -----------------------------------------


def _down(tmp_path: Path) -> SqliteTrioRetriever:
    return retriever(tmp_path, FakeEmbeddings({}, fail=TransientError("busy", dependency="gemini")))


def test_the_tool_searches_in_the_users_own_scope(tmp_path: Path) -> None:
    tracer = FakeTracer()
    ctx = turn_context(MANAGER_A, fake_ports(MANAGER_A, trios=retriever(tmp_path), tracer=tracer))

    out = search_golden_trios(SearchTriosInput(query=SEASONS), ctx)
    returns = search_golden_trios(SearchTriosInput(query=RETURNS), ctx)

    assert out.matches == []  # manager-b's Trio, never manager-a's
    assert [m.ref for m in returns.matches] == ["return-rate-by-category@1"]
    span = [s for s in tracer.spans if s.name == "search_golden_trios"][-1]
    assert span.attributes["query"] == RETURNS
    assert span.attributes["trios.refs"] == ["return-rate-by-category@1"]
    assert span.attributes["trios.scores"] == ["1.00"]
    assert span.attributes["trios.dropped"] == 0


def test_the_tool_returns_no_matches_when_the_retriever_is_down(tmp_path: Path) -> None:
    tracer = FakeTracer()
    ctx = turn_context(MANAGER_A, fake_ports(MANAGER_A, trios=_down(tmp_path), tracer=tracer))

    out = search_golden_trios(SearchTriosInput(query=RETURNS), ctx)

    assert out.matches == []
    [span] = [s for s in tracer.spans if s.name == "search_golden_trios"]
    assert span.attributes["trios.degraded"] is True


def test_the_tool_query_is_pii_filtered_on_the_span(tmp_path: Path) -> None:
    tracer = FakeTracer()
    ctx = turn_context(CEO, fake_ports(CEO, trios=retriever(tmp_path), tracer=tracer))

    search_golden_trios(SearchTriosInput(query="orders of jane.doe@example.com"), ctx)

    [span] = [s for s in tracer.spans if s.name == "search_golden_trios"]
    assert "jane.doe@example.com" not in str(span.attributes["query"])


def test_a_turn_puts_the_matches_in_state_and_on_the_trace(tmp_path: Path) -> None:
    tracer = FakeTracer()
    llm = FakeLLMFactory(
        analyst=[AIMessage(content="Here is the method.")], decision=analysis_decision(RETURNS)
    )
    ports = fake_ports(MANAGER_A, llm=llm, trios=retriever(tmp_path), tracer=tracer)
    service, graph = chat_service(ports)

    _, config = ask(service, "manager-a", "Which categories get returned most?")

    state = graph.get_state(config).values
    assert [m.ref for m in state["trios"]] == ["return-rate-by-category@1"]
    assert state["retrieval_degraded"] is False
    [node] = [s for s in tracer.spans if s.name == "trio_search"]  # inside retrieve_trios
    assert node.attributes["trios.refs"] == ["return-rate-by-category@1"]
    assert node.attributes["trios.scores"] == ["1.00"]
    assert node.attributes["trios.dropped"] == 0
    assert "<reference_trios>" in str(llm.analyst.seen[-1][0].content)


def test_a_turn_with_the_retriever_down_answers_and_is_flagged_degraded(tmp_path: Path) -> None:
    tracer = FakeTracer()
    ports = fake_ports(
        CEO, llm=_analyst_runs_sql_then_answers(), trios=_down(tmp_path), tracer=tracer
    )
    service, graph = chat_service(ports)

    [reply], config = ask(service, "ceo", "What was total revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is None
    assert graph.get_state(config).values["trios"] == []
    [root] = [s for s in tracer.spans if s.name == "turn"]
    assert root.attributes["retrieval.degraded"] is True


def test_the_analyst_can_re_search_for_a_sub_question(tmp_path: Path) -> None:
    tracer = FakeTracer()
    call = {"name": "search_golden_trios", "args": {"query": RETURNS}, "id": "c1"}
    llm = FakeLLMFactory(
        analyst=[AIMessage(content="", tool_calls=[call]), AIMessage(content="Done.")],
        decision=analysis_decision("something else entirely"),
    )
    ports = fake_ports(MANAGER_A, llm=llm, trios=retriever(tmp_path), tracer=tracer)
    service, _ = chat_service(ports)

    [reply], _ = ask(service, "manager-a", "Returns, then something else")

    assert isinstance(reply, AgentReply)
    assert reply.markdown == "Done."
    [span] = [s for s in tracer.spans if s.name == "search_golden_trios"]
    assert span.attributes["trios.refs"] == ["return-rate-by-category@1"]
    tool_results = [m for m in llm.analyst.seen[-1] if isinstance(m, ToolMessage)]
    assert '<tool_result name="search_golden_trios">' in str(tool_results[-1].content)
    assert "return-rate-by-category@1" in str(tool_results[-1].content)


# --- review fixes: sync and edge cases -------------------------------------------------------


def _seed_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "trios"
    folder.mkdir()
    for path in SEED.glob("*.yaml"):
        (folder / path.name).write_text(path.read_text())
    return folder


def _edit(path: Path, **changes: object) -> None:
    trio = yaml.safe_load(path.read_text()) | changes
    path.write_text(yaml.safe_dump(trio))


def _retriever_over(folder: Path, tmp_path: Path) -> SqliteTrioRetriever:
    return SqliteTrioRetriever(
        tmp_path / "app.db", seed=folder, embeddings=FakeEmbeddings(seed_vectors()), min_score=0.75
    )


def test_a_trio_tagged_with_no_brands_reaches_no_manager(tmp_path: Path) -> None:
    folder = _seed_copy(tmp_path)
    _edit(folder / "return-rate-by-category.yaml", scope={"all_brands": False, "brands": []})
    trios = _retriever_over(folder, tmp_path)
    no_brands = UserContext(user_id="m", role=Role.MANAGER, access_version=1)

    assert trios.search(RETURNS, MANAGER_A.scope).matches == []
    assert trios.search(RETURNS, no_brands.scope).matches == []


def test_a_new_seed_version_supersedes_the_old_one(tmp_path: Path) -> None:
    folder = _seed_copy(tmp_path)
    _retriever_over(folder, tmp_path)
    _edit(folder / "return-rate-by-category.yaml", version=2)

    trios = _retriever_over(folder, tmp_path)  # a restart after the edit

    assert refs(trios.search(RETURNS, MANAGER_A.scope)) == ["return-rate-by-category@2"]


def test_a_deleted_seed_file_is_no_longer_retrieved(tmp_path: Path) -> None:
    folder = _seed_copy(tmp_path)
    _retriever_over(folder, tmp_path)
    (folder / "return-rate-by-category.yaml").unlink()

    trios = _retriever_over(folder, tmp_path)

    assert trios.search(RETURNS, MANAGER_A.scope).matches == []


def test_a_query_vector_of_the_wrong_size_is_a_transient_error(tmp_path: Path) -> None:
    trios = retriever(tmp_path, FakeEmbeddings(seed_vectors() | {"returns?": [1.0, 0.0]}))

    with pytest.raises(TransientError):
        trios.search("returns?", MANAGER_A.scope)
