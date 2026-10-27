from enum import StrEnum

from pydantic import BaseModel, Field


class RAGScenario(StrEnum):
    PERSONAL_ASSISTANT = "personal_assistant"
    ENTERPRISE_OFFICE = "enterprise_office"
    CUSTOMER_SUPPORT = "customer_support"
    EDUCATION = "education"
    CODING_ASSISTANT = "coding_assistant"


class RAGStrategy(BaseModel):
    scenario: RAGScenario
    chunk_size: int = Field(ge=1)
    overlap: int = Field(ge=0)
    keyword_weight: float = Field(ge=0.0, le=1.0)
    vector_weight: float = Field(ge=0.0, le=1.0)
    rerank_top_n: int = Field(ge=1)
    minimum_score: float = Field(default=0.12, ge=0.0)
    notes: str = Field(min_length=1)


STRATEGY_PRESETS = {
    RAGScenario.PERSONAL_ASSISTANT: RAGStrategy(
        scenario=RAGScenario.PERSONAL_ASSISTANT,
        chunk_size=500,
        overlap=80,
        keyword_weight=0.45,
        vector_weight=0.55,
        rerank_top_n=20,
        notes="个人资料偏口语和碎片化，切分略小，向量召回稍高，保留关键词避免文件名漏召回。",
    ),
    RAGScenario.ENTERPRISE_OFFICE: RAGStrategy(
        scenario=RAGScenario.ENTERPRISE_OFFICE,
        chunk_size=700,
        overlap=100,
        keyword_weight=0.5,
        vector_weight=0.5,
        rerank_top_n=30,
        notes="企业文档结构稳定，需结合权限、部门、项目元数据过滤，再做混合检索和重排。",
    ),
    RAGScenario.CUSTOMER_SUPPORT: RAGStrategy(
        scenario=RAGScenario.CUSTOMER_SUPPORT,
        chunk_size=900,
        overlap=120,
        keyword_weight=0.6,
        vector_weight=0.4,
        rerank_top_n=40,
        notes="客服知识库要求答案命中标准话术，关键词和重排权重更高，减少相似但不合规答案。",
    ),
    RAGScenario.EDUCATION: RAGStrategy(
        scenario=RAGScenario.EDUCATION,
        chunk_size=650,
        overlap=100,
        keyword_weight=0.4,
        vector_weight=0.6,
        rerank_top_n=30,
        notes="教育场景重视概念相似和上下文解释，向量召回更重要，重排用于优先学习阶段匹配内容。",
    ),
    RAGScenario.CODING_ASSISTANT: RAGStrategy(
        scenario=RAGScenario.CODING_ASSISTANT,
        chunk_size=450,
        overlap=80,
        keyword_weight=0.65,
        vector_weight=0.35,
        rerank_top_n=25,
        notes="代码助手要强保留路径、函数名、类名、错误码等精确符号，因此关键词权重大于向量。",
    ),
}


def get_rag_strategy(scenario: RAGScenario) -> RAGStrategy:
    return STRATEGY_PRESETS[scenario]
