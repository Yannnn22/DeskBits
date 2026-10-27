from pydantic import BaseModel, Field


class KnowledgeDocument(BaseModel):
    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)
