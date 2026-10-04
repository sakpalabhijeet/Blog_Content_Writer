"""Daily YouTube script agent: curious-topic research -> 3 ideas -> (email approval) -> high-retention
Hindi/English script -> (email approval / revise / regenerate, max retries) -> final script emailed.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from pathlib import Path
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .. import hitl, llm as L, store
from ..config import get_settings
from ..mail import templates
from ..research import collect, fetchers
from ..text_utils import devanagari_ratio, narration, word_count
from . import prompts as P
from .schemas import FactSheet, RetentionReview, ScriptOutline, TopicIdeaList, YTMeta, YTState

log = logging.getLogger(__name__)
AGENT = "youtube"
HINDI_MIN_RATIO = 0.6


def hindi_ratio(script: str) -> float:
    return devanagari_ratio(narration(script))  # English production cues don't count against Hindi


def collect_daily(state: YTState) -> dict:
    items = collect.collect(AGENT, state.get("recency_days") or get_settings().yt_recency_days)
    log.info("collected %d daily items", len(items))
    return {"daily_items": items}


def propose_topics(state: YTState) -> dict:
    items = state.get("daily_items") or []
    avoid = store.recent_topics(AGENT, 30) + (state.get("rejected_titles") or [])
    feedback = state.get("topic_feedback") or ""
    result = L.structured(
        TopicIdeaList,
        [
            SystemMessage(content=P.TOPICS_SYSTEM),
            HumanMessage(content=(
                f"Today: {state['as_of']}\n\nResearch digest:\n{collect.format_digest(items[:50])}\n\n"
                f"Avoid these recent/rejected titles: {avoid or 'none'}\n"
                + (f"\nReviewer feedback on the previous ideas (must follow): {feedback}\n" if feedback else "")
            )),
        ],
        role="writer",
    )
    options = []
    for t in result.topics:
        d = t.model_dump()
        d["sources"] = [
            {k: items[i - 1].get(k) for k in ("title", "url", "published_at", "snippet", "source")}
            for i in t.source_ids if 1 <= i <= len(items)
        ]
        options.append(d)
    return {"topic_options": options}


def send_topics(state: YTState) -> dict:
    s = get_settings()
    options = [{k: v for k, v in o.items() if k != "source_ids"} for o in state["topic_options"]]
    html, text = templates.topics_email(
        f"YouTube video ideas for {state['as_of']}",
        "Curious, high-retention ideas from today's science, mystery, technology and personal-finance news.",
        options, AGENT, int(state.get("topic_retries") or 0) + 1, s.max_retries,
    )
    return hitl.send_review(state, "Pick today's YouTube topic (reply 1, 2 or 3)", html, text, "waiting_topic")


def apply_topic_reply(state: YTState) -> dict:
    d = hitl.topic_decision(state)
    reply = state.get("reply") or {}
    if d["next"] == "regenerate":
        d["rejected_titles"] = (state.get("rejected_titles") or []) + [o["title"] for o in state["topic_options"]]
    if d["next"] == "chosen":
        opt = state["topic_options"][reply["choice"] - 1]
        d.update(
            chosen=opt,
            title=opt["title"],
            language=reply.get("language") or get_settings().script_language,
            reviewer_notes=(reply.get("feedback") or "").strip(),
            draft_retries=0,
        )
        store.set_status(state["run_id"], "running", opt["title"])
    return d


def route_topic(state: YTState) -> str:
    return {"chosen": "deep_research", "regenerate": "propose_topics",
            "clarify": "clarify_topics"}.get(state["next"], "end_run")


def deep_research(state: YTState) -> dict:
    opt = state["chosen"]
    candidates = list(opt.get("sources") or [])
    candidates += fetchers.ddg_text(opt["title"], limit=6)
    candidates += fetchers.ddg_text(f"{opt['title']} facts explained", limit=4)
    sources: List[dict] = []
    for c in collect.dedupe(candidates):
        text = fetchers.fetch_page_text(c["url"], 2000)
        if text or c.get("snippet"):
            sources.append({**c, "text": text or c.get("snippet", "")})
        if len(sources) >= 5:
            break
    numbered = "\n\n".join(f"[{i}] {s['title']} ({s['url']})\n{s['text']}" for i, s in enumerate(sources, 1))
    try:
        facts = L.structured(
            FactSheet,
            [SystemMessage(content=P.FACTS_SYSTEM),
             HumanMessage(content=f"Video topic: {opt['title']}\nCuriosity gap: {opt['curiosity_gap']}\n\nSources:\n{numbered}")],
            role="writer",
        ).model_dump()
    except L.StructuredOutputError:
        facts = {"facts": [{"fact": s["text"][:300], "source_id": i} for i, s in enumerate(sources, 1)],
                 "surprising_details": [], "open_questions": []}
    return {"sources": [{k: s[k] for k in ("title", "url")} for s in sources], "facts": facts}


def _facts_text(state: YTState) -> str:
    f = state.get("facts") or {}
    lines = [f"- {x['fact']} [{x.get('source_id', 0)}]" for x in f.get("facts", [])]
    if f.get("surprising_details"):
        lines.append("Surprising details: " + "; ".join(f["surprising_details"]))
    if f.get("open_questions"):
        lines.append("Open questions: " + "; ".join(f["open_questions"]))
    return "\n".join(lines)


def outline(state: YTState) -> dict:
    opt = state["chosen"]
    feedback = state.get("feedback") if state.get("next") == "regenerate" else ""
    o = L.structured(
        ScriptOutline,
        [SystemMessage(content=P.OUTLINE_SYSTEM),
         HumanMessage(content=(
             f"Topic: {opt['title']}\nCategory: {opt['category']}\nSuggested hook: {opt['hook_line']}\n"
             f"Curiosity gap: {opt['curiosity_gap']}\nRetention angle: {opt['retention_angle']}\n"
             f"Target length: about {get_settings().yt_target_words} words (~{get_settings().yt_target_words // 140} min)\n"
             + (f"Reviewer notes: {state['reviewer_notes']}\n" if state.get("reviewer_notes") else "")
             + (f"The reviewer rejected the previous script. Feedback (must address): {feedback}\n" if feedback else "")
             + f"\nFacts:\n{_facts_text(state)}"))],
        role="writer",
    )
    return {"outline": o.model_dump(), "draft_version": int(state.get("draft_version") or 0) + 1}


def _write(state: YTState, language: str, extra: str = "") -> str:
    rule = P.HINDI_RULE if language == "hindi" else P.ENGLISH_RULE
    o = state["outline"]
    beats = "\n".join(f"- {b['heading']}: {'; '.join(b['key_points'])} (retention: {b['retention_device']})" for b in o["beats"])
    return L.generate(
        [SystemMessage(content=P.SCRIPT_SYSTEM.format(language_rule=rule, words=get_settings().yt_target_words)),
         HumanMessage(content=(
             f"Title: {o['working_title']}\nHook: {o['hook']}\nOpen loop: {o['open_loop']}\nBeats:\n{beats}\n"
             f"Twist/payoff: {o['twist_or_payoff']}\nEnding: {o['ending']}\nCTA: {o['cta']}\n\n"
             f"Facts:\n{_facts_text(state)}\n{extra}"))],
        role="script",
    )


def write_script(state: YTState) -> dict:
    language = state.get("language") or "hindi"
    notes = ""
    script = _write(state, language)
    if language == "hindi" and hindi_ratio(script) < HINDI_MIN_RATIO:
        log.info("hindi ratio %.2f too low; retrying", hindi_ratio(script))
        script = _write(state, language, "\nIMPORTANT: The previous attempt was not in Hindi. Write in Devanagari Hindi.")
        if hindi_ratio(script) < HINDI_MIN_RATIO:
            notes = "The local model could not produce a good Hindi script, so this version is in English."
            language = "english"
            script = _write(state, language)
    return {"script_md": script, "language": language, "notes": notes}


def retention_review(state: YTState) -> dict:
    script = state["script_md"]
    try:
        review = L.structured(
            RetentionReview,
            [SystemMessage(content=P.REVIEW_SYSTEM), HumanMessage(content=script[:12000])],
            role="fast",
        )
    except L.StructuredOutputError:
        return {}
    log.info("retention review: hook=%d pacing=%d rewrite=%s", review.hook_score, review.pacing_score, review.rewrite_needed)
    if not (review.rewrite_needed or review.hook_score < 7 or review.pacing_score < 7) or not review.issues:
        return {}
    rewritten = L.generate(
        [SystemMessage(content=P.REWRITE_SYSTEM),
         HumanMessage(content="Issues to fix:\n- " + "\n- ".join(review.issues) + f"\n\nScript:\n{script}")],
        role="script",
    )
    keep_lang = state.get("language") != "hindi" or hindi_ratio(rewritten) >= HINDI_MIN_RATIO
    if keep_lang and word_count(rewritten) >= 0.7 * word_count(script):
        return {"script_md": rewritten}
    return {}


def save_draft(state: YTState) -> dict:
    path = hitl.run_dir(state["run_id"]) / f"script_v{state['draft_version']}.md"
    path.write_text(state["script_md"], encoding="utf-8")
    return {"draft_path": str(path)}


def send_draft(state: YTState) -> dict:
    s = get_settings()
    md = state["script_md"]
    words = word_count(md)
    content_html, _ = templates.md_to_html(md)
    sources = "".join(f"<li><a href='{html_lib.escape(x['url'])}'>{html_lib.escape(x['title'])}</a></li>"
                      for x in state.get("sources") or [])
    if sources:
        content_html += f"<h3>Research sources</h3><ul>{sources}</ul>"
    html, text = templates.draft_email(
        AGENT, state["title"], content_html, md,
        f"{words} words · ~{max(1, round(words / 140))} min · {state.get('language', 'hindi').title()}",
        state["draft_version"], int(state.get("draft_retries") or 0) + 1, s.max_retries, state.get("notes", ""),
    )
    return hitl.send_review(state, f"YouTube script v{state['draft_version']}: {state['title']}", html, text,
                            "waiting_draft", attachments=[Path(state["draft_path"])])


def apply_draft_reply(state: YTState) -> dict:
    d = hitl.draft_decision(state)
    language = (state.get("reply") or {}).get("language")
    if language:
        d["language"] = language
    return d


def route_draft(state: YTState) -> str:
    return {"approve": "finalize", "revise": "revise_script", "regenerate": "outline",
            "clarify": "clarify_draft"}.get(state["next"], "end_run")


def revise_script(state: YTState) -> dict:
    language = state.get("language") or "hindi"
    current_lang = "hindi" if hindi_ratio(state["script_md"]) >= HINDI_MIN_RATIO else "english"
    if language != current_lang:  # reviewer asked to switch language -> rewrite from the outline
        return {**write_script({**state, "language": language}), "draft_version": int(state["draft_version"]) + 1}
    revised = L.generate(
        [SystemMessage(content=P.REVISE_SYSTEM.format(language=language)),
         HumanMessage(content=f"Reviewer feedback: {state.get('feedback', '')}\n\nFacts:\n{_facts_text(state)}\n\n"
                              f"Script:\n{state['script_md']}")],
        role="script",
    )
    return {"script_md": revised or state["script_md"], "draft_version": int(state["draft_version"]) + 1, "notes": ""}


def finalize(state: YTState) -> dict:
    script = state["script_md"]
    try:
        meta = L.structured(YTMeta, [SystemMessage(content=P.META_SYSTEM), HumanMessage(content=script[:10000])],
                            role="fast").model_dump()
    except L.StructuredOutputError:
        meta = {"titles": [state["title"]], "thumbnail_texts": [], "description": "", "tags": [], "hashtags": []}
    rd = hitl.run_dir(state["run_id"])
    slug = hitl.slugify(state["title"])
    package = (
        script.rstrip()
        + "\n\n---\n\n## Title options\n" + "\n".join(f"- {t}" for t in meta["titles"])
        + "\n\n## Thumbnail text\n" + "\n".join(f"- {t}" for t in meta["thumbnail_texts"])
        + f"\n\n## Description\n\n{meta['description']}\n\n## Tags\n\n{', '.join(meta['tags'])}\n\n"
        + " ".join(h if h.startswith("#") else f"#{h}" for h in meta["hashtags"])
        + ("\n\n## Research sources\n" + "\n".join(f"- [{x['title']}]({x['url']})" for x in state.get("sources") or []))
        + "\n"
    )
    final_md = rd / f"{slug}.md"
    final_md.write_text(package, encoding="utf-8")
    # Voice-over text: narration only (no Markdown, no production cues).
    voice = narration(script).replace("**", "")
    (rd / f"{slug}_voiceover.txt").write_text(re.sub(r"\n{3,}", "\n\n", voice).strip() + "\n", encoding="utf-8")
    return {"final_path": str(final_md), "meta": meta}


def send_final(state: YTState) -> dict:
    final_md = Path(state["final_path"])
    body = final_md.read_text(encoding="utf-8")
    content_html, _ = templates.md_to_html(body)
    meta = state["meta"]
    meta_html = (
        "<b>Title options:</b><br>" + "<br>".join(f"• {t}" for t in meta["titles"])
        + "<br><br><b>Thumbnail text:</b> " + " | ".join(meta["thumbnail_texts"])
        + f"<br><b>Words:</b> {word_count(state['script_md'])} (~{max(1, round(word_count(state['script_md']) / 140))} min)"
        + "<br><br>Attached: full script package (.md) and a clean voice-over text file (.txt)."
    )
    meta_text = re.sub(r"<[^>]+>", " ", meta_html.replace("<br>", "\n"))
    voice = final_md.with_name(final_md.stem + "_voiceover.txt")
    html, text = templates.final_email(state["title"], content_html, body, meta_html, meta_text)
    hitl.send_notice(state["run_id"], f"Final YouTube script: {state['title']}", html, text, [final_md, voice])
    store.add_topic_history(AGENT, state["title"])
    store.set_status(state["run_id"], "done", state["title"])
    return {}


def end_run(state: YTState) -> dict:
    attach = [Path(state["draft_path"])] if state.get("draft_path") else []
    return hitl.end_run(state, state.get("end_reason") or "Run ended.", attach)


def build_graph(checkpointer=None):
    g = StateGraph(YTState)
    for name, fn in [
        ("collect_daily", collect_daily), ("propose_topics", propose_topics), ("send_topics", send_topics),
        ("await_topics", hitl.await_reply("topics")), ("apply_topic_reply", apply_topic_reply),
        ("clarify_topics", hitl.send_clarify("topics")), ("deep_research", deep_research), ("outline", outline),
        ("write_script", write_script), ("retention_review", retention_review), ("save_draft", save_draft),
        ("send_draft", send_draft), ("await_draft", hitl.await_reply("draft")),
        ("apply_draft_reply", apply_draft_reply), ("clarify_draft", hitl.send_clarify("draft")),
        ("revise_script", revise_script), ("finalize", finalize), ("send_final", send_final), ("end_run", end_run),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "collect_daily")
    g.add_edge("collect_daily", "propose_topics")
    g.add_edge("propose_topics", "send_topics")
    g.add_edge("send_topics", "await_topics")
    g.add_edge("await_topics", "apply_topic_reply")
    g.add_conditional_edges("apply_topic_reply", route_topic,
                            ["deep_research", "propose_topics", "clarify_topics", "end_run"])
    g.add_edge("clarify_topics", "await_topics")
    g.add_edge("deep_research", "outline")
    g.add_edge("outline", "write_script")
    g.add_edge("write_script", "retention_review")
    g.add_edge("retention_review", "save_draft")
    g.add_edge("save_draft", "send_draft")
    g.add_edge("send_draft", "await_draft")
    g.add_edge("await_draft", "apply_draft_reply")
    g.add_conditional_edges("apply_draft_reply", route_draft,
                            ["finalize", "revise_script", "outline", "clarify_draft", "end_run"])
    g.add_edge("clarify_draft", "await_draft")
    g.add_edge("revise_script", "save_draft")
    g.add_edge("finalize", "send_final")
    g.add_edge("send_final", END)
    g.add_edge("end_run", END)
    return g.compile(checkpointer=checkpointer)
