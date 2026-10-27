from desktopmate_agent.rag.chunking import MarkdownChunker
from desktopmate_agent.rag.embeddings import (
    EmbeddingProvider,
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    create_embedding_provider,
)
from desktopmate_agent.rag.evaluation import RetrievalEvalCase, RetrievalEvalReport, evaluate_retrieval
from desktopmate_agent.rag.evaluation import RetrievalEvalRun, run_retrieval_evaluation
from desktopmate_agent.rag.hybrid import HybridRetriever
from desktopmate_agent.rag.ingestion import LocalDocumentIngestionService
from desktopmate_agent.rag.keyword_index import InMemoryBM25KeywordIndex, InMemoryKeywordIndex
from desktopmate_agent.rag.loaders import TextDocumentLoader
from desktopmate_agent.rag.milvus import MilvusCollectionConfig, MilvusVectorStore, build_milvus_schema
from desktopmate_agent.rag.models import KnowledgeChunk, KnowledgeDocument
from desktopmate_agent.rag.rerank import Reranker, RuleBasedReranker
from desktopmate_agent.rag.strategies import RAGScenario, RAGStrategy, get_rag_strategy
from desktopmate_agent.rag.vector_store import InMemoryVectorStore, RetrievalResult

__all__ = [
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "HybridRetriever",
    "InMemoryKeywordIndex",
    "InMemoryBM25KeywordIndex",
    "InMemoryVectorStore",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "LocalDocumentIngestionService",
    "MarkdownChunker",
    "MilvusCollectionConfig",
    "MilvusVectorStore",
    "RAGScenario",
    "RAGStrategy",
    "Reranker",
    "RetrievalEvalCase",
    "RetrievalEvalReport",
    "RetrievalEvalRun",
    "RetrievalResult",
    "RuleBasedReranker",
    "TextDocumentLoader",
    "build_milvus_schema",
    "create_embedding_provider",
    "evaluate_retrieval",
    "get_rag_strategy",
    "run_retrieval_evaluation",
]
