from desktopmate_agent.rag.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    create_embedding_provider,
)
from desktopmate_agent.rag.hybrid import HybridRetriever
from desktopmate_agent.rag.models import KnowledgeChunk
from desktopmate_agent.rag.rerank import RuleBasedReranker
from desktopmate_agent.rag.strategies import RAGScenario, get_rag_strategy
from desktopmate_agent.rag.keyword_index import InMemoryBM25KeywordIndex, InMemoryKeywordIndex
from desktopmate_agent.rag.vector_store import InMemoryVectorStore, RetrievalResult


def make_chunk(chunk_id: str, text: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc",
        text=text,
        chunk_index=0,
        start_char=0,
        end_char=len(text),
    )


def make_chunk_with_metadata(chunk_id: str, text: str, metadata: dict[str, object]) -> KnowledgeChunk:
    chunk = make_chunk(chunk_id, text)
    return chunk.model_copy(update={"metadata": metadata})


def test_fake_embedding_provider_returns_stable_vectors():
    provider = FakeEmbeddingProvider(dimensions=16)

    first = provider.embed_query("Milvus 配置")
    second = provider.embed_query("Milvus 配置")

    assert first == second
    assert len(first) == 16
    assert sum(value * value for value in first) > 0


def test_openai_compatible_embedding_provider_uses_client_response_vectors():
    class FakeEmbeddingsEndpoint:
        def __init__(self) -> None:
            self.requests = []

        def create(self, *, model: str, input: list[str]):
            self.requests.append({"model": model, "input": input})
            return type(
                "EmbeddingResponse",
                (),
                {
                    "data": [
                        type("EmbeddingData", (), {"embedding": [0.1, 0.2]})(),
                        type("EmbeddingData", (), {"embedding": [0.3, 0.4]})(),
                    ]
                },
            )()

    class FakeClient:
        def __init__(self) -> None:
            self.embeddings = FakeEmbeddingsEndpoint()

    client = FakeClient()
    provider = OpenAICompatibleEmbeddingProvider(model="text-embedding-3-small", client=client)

    assert provider.embed_documents(["Milvus 配置", "RAG 召回"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert client.embeddings.requests == [
        {"model": "text-embedding-3-small", "input": ["Milvus 配置", "RAG 召回"]}
    ]


def test_create_embedding_provider_selects_fake_by_default():
    provider = create_embedding_provider(provider_name="fake", dimensions=16)

    assert isinstance(provider, FakeEmbeddingProvider)
    assert len(provider.embed_query("RAG")) == 16


def test_create_embedding_provider_selects_openai_compatible_provider():
    provider = create_embedding_provider(
        provider_name="openai",
        model="text-embedding-3-small",
        api_key="test-key",
        base_url="https://example.test/v1",
    )

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)


def test_in_memory_vector_store_returns_most_similar_chunks_first():
    provider = FakeEmbeddingProvider(dimensions=32)
    store = InMemoryVectorStore(embedding_provider=provider)
    store.add_chunks(
        [
            make_chunk("rag:1", "文档切分会影响召回率和命中率"),
            make_chunk("milvus:1", "Milvus 需要先启动 Docker 服务并配置 collection"),
            make_chunk("memory:1", "长期记忆保存用户偏好"),
        ]
    )

    results = store.search("怎么配置 Milvus", limit=2)

    assert [result.chunk.chunk_id for result in results] == ["milvus:1", "rag:1"]
    assert results[0].score >= results[1].score


def test_in_memory_keyword_index_matches_exact_terms_and_metadata():
    index = InMemoryKeywordIndex()
    index.add_chunks(
        [
            make_chunk("rag:1", "文档切分会影响召回率和命中率"),
            make_chunk("milvus:1", "向量数据库配置"),
            make_chunk("memory:1", "长期记忆保存用户偏好"),
        ]
    )

    results = index.search("Milvus 配置", limit=2)

    assert [result.chunk.chunk_id for result in results] == ["milvus:1"]
    assert results[0].score > 0


def test_bm25_keyword_index_prefers_focused_chunks_over_long_chunks():
    index = InMemoryBM25KeywordIndex()
    index.add_chunks(
        [
            make_chunk("long:1", "Milvus " + "普通背景内容 " * 80),
            make_chunk("short:1", "Milvus 配置"),
        ]
    )

    results = index.search("Milvus", limit=2)

    assert [result.chunk.chunk_id for result in results] == ["short:1", "long:1"]
    assert results[0].score > results[1].score


def test_hybrid_retriever_combines_vector_and_keyword_scores():
    chunks = [
        make_chunk("rag:1", "文档切分会影响召回率和命中率"),
        make_chunk("milvus:1", "Milvus 需要先启动 Docker 服务并配置 collection"),
        make_chunk("memory:1", "长期记忆保存用户偏好"),
    ]
    retriever = HybridRetriever(
        embedding_provider=FakeEmbeddingProvider(dimensions=32),
        strategy=get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT),
    )
    retriever.add_chunks(chunks)

    results = retriever.search("怎么配置 Milvus", limit=2)

    assert [result.chunk.chunk_id for result in results] == ["milvus:1", "rag:1"]
    assert results[0].score > results[1].score


def test_hybrid_retriever_uses_bm25_keyword_index_by_default():
    retriever = HybridRetriever(
        embedding_provider=FakeEmbeddingProvider(dimensions=32),
        strategy=get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT),
    )

    assert isinstance(retriever.keyword_index, InMemoryBM25KeywordIndex)


def test_rule_based_reranker_promotes_metadata_and_exact_term_matches():
    reranker = RuleBasedReranker()
    results = [
        make_result(
            make_chunk("generic:1", "数据库服务可以用 Docker 启动"),
            score=0.9,
        ),
        make_result(
            make_chunk_with_metadata(
                "milvus:1",
                "启动 Docker 后创建 collection",
                {"title": "Milvus 配置", "source_path": "/docs/milvus.md"},
            ),
            score=0.6,
        ),
    ]

    reranked = reranker.rerank("怎么配置 Milvus", results, limit=2)

    assert [result.chunk.chunk_id for result in reranked] == ["milvus:1", "generic:1"]
    assert reranked[0].score > reranked[1].score


def test_hybrid_retriever_can_apply_reranker_to_fused_candidates():
    retriever = HybridRetriever(
        embedding_provider=FakeEmbeddingProvider(dimensions=32),
        strategy=get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT),
        reranker=RuleBasedReranker(),
    )
    retriever.add_chunks(
        [
            make_chunk("generic:1", "数据库服务可以用 Docker 启动"),
            make_chunk_with_metadata(
                "milvus:1",
                "启动 Docker 后创建 collection",
                {"title": "Milvus 配置", "source_path": "/docs/milvus.md"},
            ),
        ]
    )

    results = retriever.search("怎么配置 Milvus", limit=1)

    assert [result.chunk.chunk_id for result in results] == ["milvus:1"]


def make_result(chunk: KnowledgeChunk, *, score: float) -> RetrievalResult:
    return RetrievalResult(chunk=chunk, score=score)
