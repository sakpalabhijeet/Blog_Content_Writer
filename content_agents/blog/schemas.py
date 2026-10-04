from __future__ import annotations

import operator
from typing import Annotated, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class Task(BaseModel):
    id: int
    title: str
    goal: str = Field(
        ...,
        description="One sentence describing what the reader should be able to do/understand after this section.",
    )
    bullets: List[str] = Field(
        ..., min_length=3, max_length=6, description="3–6 concrete, non-overlapping subpoints to cover in this section."
    )
    target_words: int = Field(..., description="Target word count for this section (250–650).")
    tags: List[str] = Field(default_factory=list)
    requires_research: bool = False
    requires_citations: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str
    audience: str
    tone: str
    blog_kind: Literal["explainer", "tutorial", "news_roundup", "comparison", "system_design"] = "explainer"
    constraints: List[str] = Field(default_factory=list)
    tasks: List[Task] = Field(..., min_length=5, max_length=9)


class EvidenceItem(BaseModel):
    title: str
    url: str
    published_at: Optional[str] = None
    snippet: Optional[str] = None
    source: Optional[str] = None


class RouterDecision(BaseModel):
    needs_research: bool
    mode: Literal["closed_book", "hybrid", "open_book"]
    queries: List[str] = Field(default_factory=list)


class RelevantIds(BaseModel):
    ids: List[int] = Field(default_factory=list, description="Numbers of the relevant, authoritative results.")


class TopicOption(BaseModel):
    title: str = Field(..., description="Working blog title, specific and compelling.")
    angle: str = Field(..., description="One or two sentences: what the post covers and its unique angle.")
    why_now: str = Field(..., description="What happened this week that makes this timely.")
    dev_takeaway: str = Field(..., description="How programmers can use this in their daily work.")
    upskill_angle: str = Field(..., description="What skill/trend readers should learn to stay relevant.")
    source_ids: List[int] = Field(default_factory=list, description="Numbers of the digest items backing this topic.")


class TopicList(BaseModel):
    topics: List[TopicOption] = Field(..., min_length=3, max_length=5)


class ImageSpec(BaseModel):
    kind: Literal["diagram", "photo", "illustration"] = Field(
        ..., description="diagram = flow/architecture (rendered locally); photo = stock photo; illustration = AI image"
    )
    insert_after_heading: str = Field(..., description="Exact '## ' section heading the image belongs under.")
    alt: str
    caption: str
    search_query: str = Field("", description="2–4 word stock-photo search query (photo kind).")
    prompt: str = Field("", description="Detailed prompt for an AI image model (illustration/diagram fallback).")
    diagram_description: str = Field("", description="For diagrams: nodes and arrows to draw, short labels.")


class ImagePlan(BaseModel):
    hero_query: str = Field(..., description="2–4 word stock-photo query for the cover image.")
    hero_prompt: str = Field(..., description="Prompt for an AI-generated cover image (fallback).")
    images: List[ImageSpec] = Field(default_factory=list, max_length=3)


class DotDiagram(BaseModel):
    dot: str = Field(..., description="Complete Graphviz DOT source, e.g. digraph G { rankdir=LR; A -> B; }")


class FeedbackTargets(BaseModel):
    section_numbers: List[int] = Field(default_factory=list, description="1-based numbers of sections to rewrite.")
    add_new_section: bool = False
    new_section_title: str = ""
    instructions: str = Field("", description="Concrete rewrite instructions derived from the feedback.")


class MediumMeta(BaseModel):
    title: str = Field(..., description="Final Medium title (max ~70 chars).")
    subtitle: str = Field(..., description="Medium subtitle, one sentence.")
    tldr: List[str] = Field(..., min_length=3, max_length=5, description="Key takeaways.")
    tags: List[str] = Field(..., min_length=3, max_length=5, description="Medium tags.")
    seo_description: str = Field(..., description="~150-character search description.")


class BlogState(TypedDict, total=False):
    run_id: str
    as_of: str
    recency_days: int

    # weekly research + topic review
    weekly_items: List[dict]
    topic_options: List[dict]
    topic_retries: int
    topic_feedback: str
    rejected_titles: List[str]
    review_seq: int
    reply: dict
    next: str
    end_reason: str

    # chosen topic
    topic: str
    title: str
    chosen: dict
    images_mode: str
    user_images: List[str]

    # routing / research (from Blog_Writer.py)
    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[dict]
    plan: dict

    # workers: (draft_version, task_id, section_md)
    sections: Annotated[List[tuple], operator.add]
    section_texts: List[str]
    draft_version: int
    draft_retries: int
    feedback: str

    # images / drafts
    image_specs: List[dict]
    draft_md: str
    draft_path: str
    final_path: str
