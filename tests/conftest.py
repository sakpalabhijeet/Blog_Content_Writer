"""Test fixtures: isolated data dir, file mail backend, fake LLM and fake research (no network, no Ollama)."""
from __future__ import annotations

import re

import pytest

WORD = "insight "
HINDI = "यह कहानी बहुत रहस्यमयी है और आप अंत तक देखेंगे "


def _sys(messages) -> str:
    return messages[0].content if messages else ""


def _human(messages) -> str:
    return messages[-1].content if messages else ""


def fake_generate(messages, role="writer", temperature=None) -> str:
    system, human = _sys(messages), _human(messages)
    if "Write ONE section" in system:
        title = re.search(r"Section title: (.+)", human).group(1)
        return f"## {title}\n\n" + WORD * 300  # deliberately short -> exercises enforce_length
    if "Expand the given blog section" in system:
        heading = re.search(r"^## .+$", human, re.M).group(0)
        return f"{heading}\n\n" + WORD * 700
    if "revising one section" in system:
        heading = re.search(r"^## .+$", human, re.M).group(0)
        return f"{heading}\n\nREVISED " + WORD * 500
    if "Graphviz DOT" in system:
        return 'digraph G { rankdir=LR; "A" -> "B"; }'
    if "scriptwriter" in system:
        return "# रहस्य\n\n## HOOK (0:00–0:30)\n\n" + HINDI * 60 + "\n\n[VISUAL: dark forest]\n\n## ENDING + CTA\n\n" + HINDI * 20
    if "YouTube script editor" in system:
        return "# रहस्य v2\n\n## HOOK (0:00–0:30)\n\n" + HINDI * 70
    return "ok"


def fake_structured(schema, messages, role="fast", retries=2, temperature=None):
    from content_agents.blog import schemas as B
    from content_agents.youtube import schemas as Y

    name = schema.__name__
    if name == "TopicList":
        return B.TopicList(topics=[
            B.TopicOption(title=f"Blog topic {i}", angle="angle", why_now="news", dev_takeaway="use it",
                          upskill_angle="learn it", source_ids=[1, 2]) for i in (1, 2, 3)])
    if name == "RouterDecision":
        return B.RouterDecision(needs_research=True, mode="hybrid", queries=["q1", "q2"])
    if name == "RelevantIds":
        return B.RelevantIds(ids=[1, 2])
    if name == "Plan":
        return B.Plan(blog_title="Test Blog", audience="devs", tone="friendly", tasks=[
            B.Task(id=0, title=f"Section {i}", goal="g", bullets=["a", "b", "c"], target_words=300) for i in range(1, 7)])
    if name == "ImagePlan":
        return B.ImagePlan(hero_query="laptop code", hero_prompt="cover", images=[
            B.ImageSpec(kind="diagram", insert_after_heading="Section 2", alt="flow", caption="The flow",
                        diagram_description="A to B")])
    if name == "FeedbackTargets":
        return B.FeedbackTargets(section_numbers=[2], instructions="improve")
    if name == "MediumMeta":
        return B.MediumMeta(title="Final Test Blog", subtitle="Sub", tldr=["a", "b", "c"], tags=["AI", "Dev", "LLM"],
                            seo_description="seo")
    if name == "TopicIdeaList":
        return Y.TopicIdeaList(topics=[
            Y.TopicIdea(title=f"Video {i}", category="mystery", hook_line="hook", curiosity_gap="gap",
                        retention_angle="twist", why_now="today", source_ids=[1]) for i in (1, 2, 3)])
    if name == "FactSheet":
        return Y.FactSheet(facts=[Y.Fact(fact=f"fact {i}", source_id=1) for i in range(3)])
    if name == "ScriptOutline":
        return Y.ScriptOutline(working_title="रहस्य", hook="h", open_loop="o", twist_or_payoff="t", ending="e", cta="c",
                               beats=[Y.Beat(heading=f"b{i}", key_points=["x", "y"], retention_device="loop") for i in range(3)])
    if name == "RetentionReview":
        return Y.RetentionReview(hook_score=9, pacing_score=9, issues=[], rewrite_needed=False)
    if name == "YTMeta":
        return Y.YTMeta(titles=["T1", "T2", "T3"], thumbnail_texts=["A", "B"], description="d",
                        tags=["a", "b", "c", "d", "e"], hashtags=["#x", "#y"])
    if name == "_Classification":
        return schema(action="unclear")
    raise AssertionError(f"unexpected schema {name}")


ITEMS = [
    {"title": f"Item {i}", "url": f"https://example.com/{i}", "snippet": "s", "published_at": "2026-09-20", "source": "Test"}
    for i in range(1, 6)
]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAIL_BACKEND", "file")
    monkeypatch.setenv("MAX_RETRIES", "3")
    for k in ("PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(k, "")
    from content_agents.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fakes(monkeypatch):
    from content_agents import llm
    from content_agents.images import providers
    from content_agents.research import collect, fetchers

    monkeypatch.setattr(llm, "generate", fake_generate)
    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(collect, "collect", lambda agent, days, **kw: list(ITEMS))
    monkeypatch.setattr(fetchers, "ddg_text", lambda q, limit=8, days=None: ITEMS[:3])
    monkeypatch.setattr(fetchers, "ddg_news", lambda q, days=7, limit=8: ITEMS[3:])
    monkeypatch.setattr(fetchers, "fetch_page_text", lambda url, max_chars=3000: "page text")
    monkeypatch.setattr(providers, "graphviz_available", lambda: False)
