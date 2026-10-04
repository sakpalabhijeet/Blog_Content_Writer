from __future__ import annotations

from typing import List, Literal, TypedDict

from pydantic import BaseModel, Field


class TopicIdea(BaseModel):
    title: str = Field(..., description="Clickable working video title (English).")
    category: Literal["mystery", "science", "technology", "finance", "curiosity"]
    hook_line: str = Field(..., description="The first sentence the narrator says; creates instant curiosity.")
    curiosity_gap: str = Field(..., description="The question viewers will stay till the end to get answered.")
    retention_angle: str = Field(..., description="Why viewers keep watching: twist, reveal, practical payoff.")
    why_now: str = Field(..., description="Why this is fresh or trending today.")
    source_ids: List[int] = Field(default_factory=list, description="Digest item numbers backing this idea.")


class TopicIdeaList(BaseModel):
    topics: List[TopicIdea] = Field(..., min_length=3, max_length=5)


class Fact(BaseModel):
    fact: str
    source_id: int = Field(0, description="Number of the source this fact comes from (0 if general knowledge).")


class FactSheet(BaseModel):
    facts: List[Fact] = Field(..., min_length=3, max_length=15)
    surprising_details: List[str] = Field(default_factory=list, max_length=6)
    open_questions: List[str] = Field(default_factory=list, max_length=4)


class Beat(BaseModel):
    heading: str
    key_points: List[str] = Field(..., min_length=2, max_length=5)
    retention_device: str = Field(..., description="Open loop, pattern interrupt, mini-cliffhanger, question, etc.")


class ScriptOutline(BaseModel):
    working_title: str
    hook: str = Field(..., description="What happens in the first 30 seconds (the promise + open loop).")
    open_loop: str = Field(..., description="The big question that is only answered at the end.")
    beats: List[Beat] = Field(..., min_length=3, max_length=6)
    twist_or_payoff: str
    ending: str = Field(..., description="How the open loop is closed + a callback to the hook.")
    cta: str = Field(..., description="Call to action (comment question, subscribe, next video).")
    est_minutes: int = Field(8, ge=3, le=20)


class RetentionReview(BaseModel):
    hook_score: int = Field(..., ge=1, le=10)
    pacing_score: int = Field(..., ge=1, le=10)
    issues: List[str] = Field(default_factory=list, max_length=6)
    rewrite_needed: bool


class YTMeta(BaseModel):
    titles: List[str] = Field(..., min_length=3, max_length=5)
    thumbnail_texts: List[str] = Field(..., min_length=2, max_length=4, description="2–4 word thumbnail text options.")
    description: str
    tags: List[str] = Field(..., min_length=5, max_length=15)
    hashtags: List[str] = Field(..., min_length=2, max_length=5)


class YTState(TypedDict, total=False):
    run_id: str
    as_of: str
    recency_days: int

    daily_items: List[dict]
    topic_options: List[dict]
    topic_retries: int
    topic_feedback: str
    rejected_titles: List[str]
    review_seq: int
    reply: dict
    next: str
    end_reason: str

    chosen: dict
    title: str
    language: str
    reviewer_notes: str
    sources: List[dict]
    facts: dict
    outline: dict
    script_md: str
    notes: str
    draft_version: int
    draft_retries: int
    feedback: str
    draft_path: str
    final_path: str
    meta: dict
