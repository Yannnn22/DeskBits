from pydantic import BaseModel, Field


class RetrievalEvalRetriever:
    def search(self, query: str, *, limit: int = 5):
        raise NotImplementedError


class RetrievalEvalCase(BaseModel):
    query: str = Field(min_length=1)
    relevant_chunk_ids: set[str] = Field(min_length=1)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)


class RetrievalEvalReport(BaseModel):
    k: int = Field(ge=1)
    case_count: int = Field(ge=0)
    hit_rate: float = Field(ge=0.0, le=1.0)
    mrr: float = Field(ge=0.0, le=1.0)


class RetrievalEvalRun(BaseModel):
    report: RetrievalEvalReport
    cases: list[RetrievalEvalCase]


def run_retrieval_evaluation(
    retriever: RetrievalEvalRetriever,
    cases: list[RetrievalEvalCase],
    *,
    k: int,
) -> RetrievalEvalRun:
    evaluated_cases = [
        case.model_copy(
            update={
                "retrieved_chunk_ids": [
                    result.chunk.chunk_id for result in retriever.search(case.query, limit=k)
                ]
            }
        )
        for case in cases
    ]
    return RetrievalEvalRun(
        report=evaluate_retrieval(evaluated_cases, k=k),
        cases=evaluated_cases,
    )


def evaluate_retrieval(cases: list[RetrievalEvalCase], *, k: int) -> RetrievalEvalReport:
    if k < 1:
        raise ValueError("k must be positive")
    if not cases:
        return RetrievalEvalReport(k=k, case_count=0, hit_rate=0.0, mrr=0.0)

    hit_count = 0
    reciprocal_rank_sum = 0.0
    for case in cases:
        top_k = case.retrieved_chunk_ids[:k]
        first_relevant_rank = _first_relevant_rank(top_k, case.relevant_chunk_ids)
        if first_relevant_rank is not None:
            hit_count += 1
            reciprocal_rank_sum += 1 / first_relevant_rank

    case_count = len(cases)
    return RetrievalEvalReport(
        k=k,
        case_count=case_count,
        hit_rate=hit_count / case_count,
        mrr=reciprocal_rank_sum / case_count,
    )


def _first_relevant_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> int | None:
    for index, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in relevant_ids:
            return index
    return None
