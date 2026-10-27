from pytest import approx

from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.rag.evaluation import RetrievalEvalCase, evaluate_retrieval, run_retrieval_evaluation
from desktopmate_agent.rag.hybrid import HybridRetriever
from desktopmate_agent.rag.models import KnowledgeChunk
from desktopmate_agent.rag.strategies import RAGScenario, get_rag_strategy


def make_chunk(chunk_id: str, text: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc",
        text=text,
        chunk_index=0,
        start_char=0,
        end_char=len(text),
    )


def test_evaluate_retrieval_computes_hit_rate_and_mrr_at_k():
    cases = [
        RetrievalEvalCase(
            query="怎么启动 DesktopMate",
            relevant_chunk_ids={"install:1"},
            retrieved_chunk_ids=["overview:1", "install:1", "faq:1"],
        ),
        RetrievalEvalCase(
            query="DeepSeek API Key 放哪里",
            relevant_chunk_ids={"env:1"},
            retrieved_chunk_ids=["env:1", "install:1"],
        ),
        RetrievalEvalCase(
            query="怎么配置 Milvus",
            relevant_chunk_ids={"milvus:1"},
            retrieved_chunk_ids=["rag:1", "chunking:1"],
        ),
    ]

    report = evaluate_retrieval(cases, k=2)

    assert report.case_count == 3
    assert report.k == 2
    assert report.hit_rate == approx(2 / 3)
    assert report.mrr == approx((1 / 2 + 1 + 0) / 3)


def test_run_retrieval_evaluation_searches_retriever_and_scores_cases():
    retriever = HybridRetriever(
        embedding_provider=FakeEmbeddingProvider(dimensions=32),
        strategy=get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT),
    )
    retriever.add_chunks(
        [
            make_chunk("milvus:1", "Milvus 需要先启动 Docker 服务并配置 collection"),
            make_chunk("memory:1", "长期记忆保存用户偏好"),
        ]
    )
    cases = [
        RetrievalEvalCase(query="怎么配置 Milvus", relevant_chunk_ids={"milvus:1"}),
        RetrievalEvalCase(query="用户偏好保存在哪里", relevant_chunk_ids={"memory:1"}),
    ]

    run = run_retrieval_evaluation(retriever, cases, k=1)

    assert run.report.case_count == 2
    assert run.report.hit_rate == approx(1.0)
    assert run.report.mrr == approx(1.0)
    assert run.cases[0].retrieved_chunk_ids == ["milvus:1"]
    assert run.cases[1].retrieved_chunk_ids == ["memory:1"]


def test_rag_strategy_presets_capture_business_tradeoffs():
    personal = get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT)
    customer_support = get_rag_strategy(RAGScenario.CUSTOMER_SUPPORT)
    coding = get_rag_strategy(RAGScenario.CODING_ASSISTANT)

    assert personal.chunk_size < customer_support.chunk_size
    assert customer_support.rerank_top_n > personal.rerank_top_n
    assert coding.keyword_weight > coding.vector_weight
    assert "路径" in coding.notes
