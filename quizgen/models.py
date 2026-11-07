"""Pydantic models for the API (design section 2) and the quiz schema
(design section 3)."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=120)
    num_questions: int = Field(10, ge=5, le=10)
    language: str = Field("en", pattern="^(en|id)$")


class SourceRef(BaseModel):
    id: str
    title: str
    url: str
    fetched: bool
    chars: int


class Question(BaseModel):
    id: str
    question: str
    options: Dict[str, str]
    correct: str
    explanation: str
    difficulty: str
    grounded: bool
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    source_title: Optional[str] = None
    evidence: Optional[str] = None
    flags: List[str] = []


class Quiz(BaseModel):
    schema_version: int = 1
    id: str
    topic: str
    language: str
    generated_at: str
    grounding: str
    search: Dict
    model: Dict
    validation: Dict
    questions: List[Question]
