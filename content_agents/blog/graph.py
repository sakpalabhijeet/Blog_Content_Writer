"""Weekly Medium blog agent: research -> 3 topics -> (email approval) -> 2500+ word draft with images
-> (email approval / revise / regenerate, max retries) -> final post emailed.

Planner/worker/reducer logic is carried over from the original Blog_Writer.py.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .. import hitl, llm as L, store
from ..config import get_settings
from ..images import pipeline
from ..mail import templates
from ..research import collect, fetchers
from ..text_utils import extract_links, headings, word_count
from . import prompts as P
from .schemas import (
    BlogState, EvidenceItem, FeedbackTargets, ImagePlan, MediumMeta, Plan, RelevantIds, RouterDecision, Task, TopicList,
)

log = logging.getLogger(__name__)
AGENT = "blog"


def _images_dir(state) -> Path:
    return hitl.run_dir(state["run_id"]) / "images"


def _assemble(title: str, texts: List[str]) -> str:
    return f"# {title}\n\n" + "\n\n".join(t.strip() for t in texts) + "\n"


def _evidence_text(evidence: List[dict], limit: int = 16) -> str:
    return "\n".join(
        f"- {e['title']} | {e['url']} | {e.get('published_at') or 'date:unknown'} | {(e.get('snippet') or '')[:200]}"
        for e in evidence[:limit]
    )


# ---------------------------------------------------------------- weekly research + topics
def collect_weekly(state: BlogState) -> dict:
    items = collect.collect(AGENT, state.get("recency_days") or get_settings().blog_recency_days)
    log.info("collected %d weekly items", len(items))
    return {"weekly_items": items}


def propose_topics(state: BlogState) -> dict:
    items = state.get("weekly_items") or []
    avoid = store.recent_topics(AGENT, 60) + (state.get("rejected_titles") or [])
    feedback = state.get("topic_feedback") or ""
    result = L.structured(
        TopicList,
        [
            SystemMessage(content=P.TOPICS_SYSTEM),
            HumanMessage(
                content=(
                    f"Today: {state['as_of']}\n\nResearch digest (last {state.get('recency_days', 7)} days):\n"
                    f"{collect.format_digest(items[:50])}\n\n"
                    f"Avoid these recently published/rejected titles: {avoid or 'none'}\n"
                    + (f"\nReviewer feedback on the previous suggestions (must follow): {feedback}\n" if feedback else "")
                )
            ),
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


def send_topics(state: BlogState) -> dict:
    s = get_settings()
    options = [{k: v for k, v in o.items() if k != "source_ids"} for o in state["topic_options"]]
    html, text = templates.topics_email(
        f"AI blog topics for the week of {state['as_of']}",
        f"Based on {len(state.get('weekly_items') or [])} fresh items from AI newsletters, blogs, GitHub, "
        "Hacker News and Reddit, here are this week's suggestions.",
        options, AGENT, int(state.get("topic_retries") or 0) + 1, s.max_retries,
    )
    return hitl.send_review(state, "Pick this week's blog topic (reply 1, 2 or 3)", html, text, "waiting_topic")


def apply_topic_reply(state: BlogState) -> dict:
    d = hitl.topic_decision(state)
    reply = state.get("reply") or {}
    if d["next"] == "regenerate":
        d["rejected_titles"] = (state.get("rejected_titles") or []) + [o["title"] for o in state["topic_options"]]
    if d["next"] == "chosen":
        opt = state["topic_options"][reply["choice"] - 1]
        user_images = reply.get("attachments") or []
        notes = (reply.get("feedback") or "").strip()
        d.update(
            chosen=opt,
            title=opt["title"],
            topic=f"{opt['title']} — {opt['angle']} Developer takeaway: {opt['dev_takeaway']} "
                  f"Upskilling angle: {opt['upskill_angle']}" + (f" Reviewer notes: {notes}" if notes else ""),
            images_mode=reply.get("images_mode") or ("manual" if user_images else "auto"),
            user_images=user_images,
            evidence=[EvidenceItem(**{k: s.get(k) for k in EvidenceItem.model_fields}).model_dump()
                      for s in opt.get("sources", []) if s.get("url")],
            draft_retries=0,
        )
        store.set_status(state["run_id"], "running", opt["title"])
    return d


def route_topic(state: BlogState) -> str:
    return {"chosen": "router", "regenerate": "propose_topics", "clarify": "clarify_topics"}.get(state["next"], "end_run")


# ---------------------------------------------------------------- planning (from Blog_Writer.py)
def router_node(state: BlogState) -> dict:
    decision = L.structured(
        RouterDecision,
        [SystemMessage(content=P.ROUTER_SYSTEM),
         HumanMessage(content=f"Topic: {state['topic']}\nAs of: {state['as_of']}")],
        role="fast",
    )
    return {"needs_research": decision.needs_research, "mode": decision.mode, "queries": decision.queries[:6]}


def route_next(state: BlogState) -> str:
    return "research" if state["needs_research"] else "orchestrator"


def research_node(state: BlogState) -> dict:
    seed = list(state.get("evidence") or [])
    days = state.get("recency_days") or 7
    raw: List[dict] = []
    for q in state.get("queries") or []:
        raw.extend(fetchers.ddg_news(q, days=max(days, 14), limit=5) if state.get("mode") == "open_book" else [])
        raw.extend(fetchers.ddg_text(q, limit=6))
    raw = collect.dedupe(raw)
    if not raw:
        return {"evidence": seed}
    try:
        picked = L.structured(
            RelevantIds,
            [SystemMessage(content=P.RESEARCH_SYSTEM),
             HumanMessage(content=f"Topic: {state['topic']}\n\nResults:\n{collect.format_digest(raw)}")],
            role="fast",
        ).ids
        chosen = [raw[i - 1] for i in picked if 1 <= i <= len(raw)] or raw[:12]
    except L.StructuredOutputError:
        chosen = raw[:12]
    evidence = collect.dedupe(seed + chosen)
    return {"evidence": [EvidenceItem(**{k: e.get(k) for k in EvidenceItem.model_fields}).model_dump() for e in evidence]}


def orchestrator_node(state: BlogState) -> dict:
    s = get_settings()
    min_total = int(s.blog_min_words * 1.15)
    feedback = state.get("feedback") if state.get("next") == "regenerate" else ""
    plan = L.structured(
        Plan,
        [
            SystemMessage(content=P.ORCH_SYSTEM.format(min_total=min_total)),
            HumanMessage(
                content=(
                    f"Topic: {state['topic']}\nMode: {state.get('mode', 'closed_book')}\nAs of: {state['as_of']}\n\n"
                    f"Evidence (ONLY use for fresh claims; may be empty):\n{_evidence_text(state.get('evidence') or [])}\n"
                    + (f"\nThe reviewer rejected the previous draft. Their feedback (must address): {feedback}\n" if feedback else "")
                )
            ),
        ],
        role="writer",
    )
    # Deterministic guardrails: sequential ids and a word budget that clears the minimum.
    total = sum(t.target_words for t in plan.tasks) or 1
    scale = max(1.0, min_total / total)
    for i, t in enumerate(plan.tasks, 1):
        t.id = i
        t.target_words = max(250, min(700, int(t.target_words * scale)))
    return {"plan": plan.model_dump(), "draft_version": int(state.get("draft_version") or 0) + 1}


def fanout(state: BlogState):
    return [
        Send(
            "worker",
            {
                "task": task,
                "topic": state["topic"],
                "mode": state["mode"],
                "as_of": state["as_of"],
                "plan": state["plan"],
                "evidence": state.get("evidence") or [],
                "draft_version": state["draft_version"],
            },
        )
        for task in state["plan"]["tasks"]
    ]


def worker_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = payload.get("evidence") or []
    bullets_text = "\n- " + "\n- ".join(task.bullets)
    section_md = L.generate(
        [
            SystemMessage(content=P.WORKER_SYSTEM),
            HumanMessage(
                content=(
                    f"Blog title: {plan.blog_title}\nAudience: {plan.audience}\nTone: {plan.tone}\n"
                    f"Blog kind: {plan.blog_kind}\nConstraints: {plan.constraints}\nTopic: {payload['topic']}\n"
                    f"Mode: {payload.get('mode', 'closed_book')}\nAs of: {payload['as_of']}\n"
                    f"All sections: {[t.title for t in plan.tasks]}\n\n"
                    f"Section title: {task.title}\nGoal: {task.goal}\nTarget words: {task.target_words}\n"
                    f"Tags: {task.tags}\nrequires_research: {task.requires_research}\n"
                    f"requires_citations: {task.requires_citations}\nrequires_code: {task.requires_code}\n"
                    f"Bullets:{bullets_text}\n\n"
                    f"Evidence (ONLY use these URLs when citing):\n{_evidence_text(evidence, 20)}\n"
                )
            ),
        ],
        role="writer",
    )
    if not section_md.lstrip().startswith("## "):
        body = re.sub(r"^#+\s.*\n", "", section_md.lstrip(), count=1)  # drop a stray H1/H3 heading
        section_md = f"## {task.title}\n\n{body}"
    return {"sections": [(payload["draft_version"], task.id, section_md)]}


def merge_content(state: BlogState) -> dict:
    v = state["draft_version"]
    ordered = [md for ver, _, md in sorted(state["sections"], key=lambda x: (x[0], x[1])) if ver == v]
    return {"section_texts": ordered}


def enforce_length(state: BlogState) -> dict:
    """Guarantee >= BLOG_MIN_WORDS by expanding the most under-target sections (max 2 passes)."""
    s = get_settings()
    texts = list(state["section_texts"])
    targets = {i: t["target_words"] for i, t in enumerate(state["plan"]["tasks"])}
    for _ in range(2):
        total = sum(word_count(t) for t in texts)
        if total >= s.blog_min_words:
            break
        deficit = s.blog_min_words - total
        order = sorted(range(len(texts)), key=lambda i: word_count(texts[i]) / max(targets.get(i, 400), 1))
        picks = order[: min(3, len(order))]
        for i in picks:
            current = word_count(texts[i])
            goal = current + deficit // len(picks) + 120
            expanded = L.generate(
                [SystemMessage(content=P.EXPAND_SYSTEM),
                 HumanMessage(content=f"Blog topic: {state['topic']}\nExpand to about {goal} words.\n\n{texts[i]}")],
                role="writer",
            )
            if word_count(expanded) > current and expanded.lstrip().startswith("## "):
                texts[i] = expanded
        log.info("enforce_length: %d -> %d words", total, sum(word_count(t) for t in texts))
    return {"section_texts": texts}


# ---------------------------------------------------------------- images
def _fix_dot(dot: str, error: str) -> str:
    return _extract_dot(L.generate(
        [SystemMessage(content=P.DOT_SYSTEM),
         HumanMessage(content=f"This DOT failed to render with error:\n{error}\n\nFix it and output only DOT:\n{dot}")],
        role="fast",
    ))


def _extract_dot(text: str) -> str:
    m = re.search(r"((?:strict\s+)?(?:di)?graph\b[^{]*\{.*\})", text, re.DOTALL)
    return m.group(1) if m else ""


def decide_images(state: BlogState) -> dict:
    mode = state.get("images_mode") or "auto"
    if mode == "none":
        return {"image_specs": []}
    if state.get("next") == "revise" and state.get("image_specs"):
        return {}  # keep the images of the draft being revised
    if mode == "manual" and state.get("user_images"):
        return {"image_specs": []}
    texts = state["section_texts"]
    heads = [headings(t)[0] if headings(t) else f"Section {i + 1}" for i, t in enumerate(texts)]
    outline = "\n\n".join(f"## {h}\n{t.split(chr(10), 1)[-1][:350]}" for h, t in zip(heads, texts))
    try:
        plan = L.structured(
            ImagePlan,
            [SystemMessage(content=P.DECIDE_IMAGES_SYSTEM),
             HumanMessage(content=f"Blog title: {state['title']}\nSection headings: {heads}\n\nOutline:\n{outline}")],
            role="fast",
        )
    except L.StructuredOutputError as e:
        log.warning("image planning failed: %s", e)
        return {"image_specs": []}

    specs = [{
        "kind": "photo", "insert_after_heading": pipeline.TOP, "filename": "cover.jpg",
        "alt": state["title"], "caption": "", "search_query": plan.hero_query, "prompt": plan.hero_prompt,
    }]
    for img in plan.images:
        spec = img.model_dump()
        ext = ".jpg" if img.kind == "photo" else ".png"
        spec["filename"] = f"{hitl.slugify(img.alt, 40)}{ext}"
        if img.kind == "diagram" and mode == "auto":
            spec["dot_source"] = _extract_dot(L.generate(
                [SystemMessage(content=P.DOT_SYSTEM),
                 HumanMessage(content=f"Diagram for a blog post: {img.caption}\nDraw: {img.diagram_description or img.alt}")],
                role="fast",
            ))
            spec["prompt"] = spec["prompt"] or f"Clean flat technical diagram: {img.diagram_description or img.caption}"
        specs.append(spec)
    return {"image_specs": specs}


def resolve_images(state: BlogState) -> dict:
    md = _assemble(state["plan"]["blog_title"], state["section_texts"])
    mode = state.get("images_mode") or "auto"
    images_dir = _images_dir(state)
    if state.get("user_images"):
        md = pipeline.place_user_images(md, state["user_images"], images_dir)
    elif mode == "auto":
        md = pipeline.place_auto(md, state.get("image_specs") or [], images_dir, fix_dot=_fix_dot)
    elif mode == "manual":
        md = pipeline.place_suggestions(md, state.get("image_specs") or [])
    path = hitl.run_dir(state["run_id"]) / f"draft_v{state['draft_version']}.md"
    path.write_text(md, encoding="utf-8")
    return {"draft_md": md, "draft_path": str(path)}


# ---------------------------------------------------------------- draft review
def send_draft(state: BlogState) -> dict:
    s = get_settings()
    md = state["draft_md"]
    words = word_count(md)
    n_sections = len(headings(md))
    n_images = md.count("![")
    content_html, inline = templates.md_to_html(md, hitl.run_dir(state["run_id"]))
    notes = ""
    if words < s.blog_min_words:
        notes = f"Note: the draft is {words} words, below the {s.blog_min_words}-word target. Reply REVISE to expand it."
    html, text = templates.draft_email(
        AGENT, state["plan"]["blog_title"], content_html, md,
        f"{words} words · {n_sections} sections · {n_images} images", state["draft_version"],
        int(state.get("draft_retries") or 0) + 1, s.max_retries, notes,
    )
    return hitl.send_review(state, f"Blog draft v{state['draft_version']}: {state['plan']['blog_title']}",
                            html, text, "waiting_draft", attachments=[Path(state["draft_path"])], inline=inline)


def apply_draft_reply(state: BlogState) -> dict:
    d = hitl.draft_decision(state)
    attachments = (state.get("reply") or {}).get("attachments") or []
    if attachments and d["next"] in ("revise", "regenerate"):
        d["user_images"] = attachments
        d["images_mode"] = "manual"
    return d


def route_draft(state: BlogState) -> str:
    return {"approve": "finalize", "revise": "revise_draft", "regenerate": "orchestrator",
            "clarify": "clarify_draft"}.get(state["next"], "end_run")


def revise_draft(state: BlogState) -> dict:
    """Re-evaluate the current draft: rewrite only the sections the feedback targets."""
    texts = list(state["section_texts"])
    feedback = state.get("feedback") or ""
    numbered = "\n".join(f"{i + 1}. {headings(t)[0] if headings(t) else t[:60]}" for i, t in enumerate(texts))
    try:
        targets = L.structured(
            FeedbackTargets,
            [SystemMessage(content=P.FEEDBACK_SYSTEM),
             HumanMessage(content=f"Sections:\n{numbered}\n\nReviewer feedback:\n{feedback}")],
            role="fast",
        )
    except L.StructuredOutputError:
        targets = FeedbackTargets(section_numbers=list(range(1, len(texts) + 1)), instructions=feedback)
    idx = sorted({n - 1 for n in targets.section_numbers if 1 <= n <= len(texts)}) or list(range(len(texts)))
    for i in idx:
        texts[i] = L.generate(
            [SystemMessage(content=P.REVISE_SYSTEM),
             HumanMessage(content=(
                 f"Blog title: {state['plan']['blog_title']}\nAll sections:\n{numbered}\n\n"
                 f"Reviewer feedback: {feedback}\nEditor instructions: {targets.instructions}\n\n"
                 f"Section to revise:\n{texts[i]}"))],
            role="writer",
        ) or texts[i]
    if targets.add_new_section and targets.new_section_title:
        new = L.generate(
            [SystemMessage(content=P.WORKER_SYSTEM),
             HumanMessage(content=(
                 f"Blog title: {state['plan']['blog_title']}\nTopic: {state['topic']}\nSection title: "
                 f"{targets.new_section_title}\nGoal: address this reviewer feedback: {feedback}\nTarget words: 400\n"
                 f"Evidence (ONLY use these URLs when citing):\n{_evidence_text(state.get('evidence') or [])}"))],
            role="writer",
        )
        texts.insert(max(len(texts) - 1, 0), new)  # before the conclusion
    return {"section_texts": texts, "draft_version": int(state["draft_version"]) + 1}


# ---------------------------------------------------------------- finalize
def finalize(state: BlogState) -> dict:
    md = state["draft_md"]
    try:
        meta = L.structured(MediumMeta, [SystemMessage(content=P.MEDIUM_SYSTEM), HumanMessage(content=md[:14000])],
                            role="fast")
    except L.StructuredOutputError:
        meta = MediumMeta(title=state["plan"]["blog_title"], subtitle="", tldr=["-", "-", "-"],
                          tags=["AI", "Programming", "Software Development"], seo_description="")
    lines = md.split("\n")
    h1 = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), 0)
    lines[h1] = f"# {meta.title}\n\n*{meta.subtitle}*" if meta.subtitle else f"# {meta.title}"
    body = "\n".join(lines)

    tldr = "> **TL;DR**\n" + "\n".join(f"> - {t}" for t in meta.tldr)
    first_h2 = body.find("\n## ")
    body = body[:first_h2] + "\n\n" + tldr + "\n" + body[first_h2:] if first_h2 != -1 else body + "\n\n" + tldr

    links = extract_links(body)
    if links:
        refs = "\n".join(f"- [{label}]({url})" for label, url in links)
        body = body.rstrip() + f"\n\n## Sources\n\n{refs}\n"
    elif state.get("evidence"):
        refs = "\n".join(f"- [{e['title']}]({e['url']})" for e in state["evidence"][:8])
        body = body.rstrip() + f"\n\n## Further reading\n\n{refs}\n"

    rd = hitl.run_dir(state["run_id"])
    final_md = rd / f"{hitl.slugify(meta.title)}.md"
    final_md.write_text(body, encoding="utf-8")
    html_body, _ = templates.md_to_html(body)
    (rd / "final.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>{meta.title}</title>"
        f"<body style='max-width:760px;margin:auto;font-family:Georgia,serif;line-height:1.7'>{html_body}</body>",
        encoding="utf-8",
    )
    return {"final_path": str(final_md), "title": meta.title, "chosen": {**state["chosen"], "meta": meta.model_dump()}}


def send_final(state: BlogState) -> dict:
    rd = hitl.run_dir(state["run_id"])
    body = Path(state["final_path"]).read_text(encoding="utf-8")
    content_html, inline = templates.md_to_html(body, rd)
    meta = state["chosen"]["meta"]
    meta_html = (
        f"<b>Title:</b> {meta['title']}<br><b>Subtitle:</b> {meta['subtitle']}<br>"
        f"<b>Tags:</b> {', '.join(meta['tags'])}<br><b>SEO description:</b> {meta['seo_description']}<br>"
        f"<b>Words:</b> {word_count(body)}<br><br><b>Publishing on Medium:</b> copy the post below into the Medium "
        "editor (or open the attached final.html in a browser and copy from there), then drag the attached "
        "images into place. Set the tags and SEO description in Medium's story settings."
    )
    meta_text = re.sub(r"<[^>]+>", " ", meta_html.replace("<br>", "\n"))
    attachments = [Path(state["final_path"]), rd / "final.html"]
    images_dir = _images_dir(state)
    if images_dir.exists() and any(images_dir.iterdir()):
        attachments.append(Path(shutil.make_archive(str(rd / "images"), "zip", images_dir)))
    html, text = templates.final_email(meta["title"], content_html, body, meta_html, meta_text)
    hitl.send_notice(state["run_id"], f"Final blog post: {meta['title']}", html, text, attachments, inline)
    store.add_topic_history(AGENT, meta["title"])
    store.set_status(state["run_id"], "done", meta["title"])
    return {}


def end_run(state: BlogState) -> dict:
    attach = [Path(state["draft_path"])] if state.get("draft_path") else []
    return hitl.end_run(state, state.get("end_reason") or "Run ended.", attach)


# ---------------------------------------------------------------- graph
def build_graph(checkpointer=None):
    g = StateGraph(BlogState)
    g.add_node("collect_weekly", collect_weekly)
    g.add_node("propose_topics", propose_topics)
    g.add_node("send_topics", send_topics)
    g.add_node("await_topics", hitl.await_reply("topics"))
    g.add_node("apply_topic_reply", apply_topic_reply)
    g.add_node("clarify_topics", hitl.send_clarify("topics"))
    g.add_node("router", router_node)
    g.add_node("research", research_node)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("worker", worker_node)
    g.add_node("merge_content", merge_content)
    g.add_node("enforce_length", enforce_length)
    g.add_node("decide_images", decide_images)
    g.add_node("resolve_images", resolve_images)
    g.add_node("send_draft", send_draft)
    g.add_node("await_draft", hitl.await_reply("draft"))
    g.add_node("apply_draft_reply", apply_draft_reply)
    g.add_node("clarify_draft", hitl.send_clarify("draft"))
    g.add_node("revise_draft", revise_draft)
    g.add_node("finalize", finalize)
    g.add_node("send_final", send_final)
    g.add_node("end_run", end_run)

    g.add_edge(START, "collect_weekly")
    g.add_edge("collect_weekly", "propose_topics")
    g.add_edge("propose_topics", "send_topics")
    g.add_edge("send_topics", "await_topics")
    g.add_edge("await_topics", "apply_topic_reply")
    g.add_conditional_edges("apply_topic_reply", route_topic,
                            ["router", "propose_topics", "clarify_topics", "end_run"])
    g.add_edge("clarify_topics", "await_topics")

    g.add_conditional_edges("router", route_next, {"research": "research", "orchestrator": "orchestrator"})
    g.add_edge("research", "orchestrator")
    g.add_conditional_edges("orchestrator", fanout, ["worker"])
    g.add_edge("worker", "merge_content")
    g.add_edge("merge_content", "enforce_length")
    g.add_edge("enforce_length", "decide_images")
    g.add_edge("decide_images", "resolve_images")
    g.add_edge("resolve_images", "send_draft")
    g.add_edge("send_draft", "await_draft")
    g.add_edge("await_draft", "apply_draft_reply")
    g.add_conditional_edges("apply_draft_reply", route_draft,
                            ["finalize", "revise_draft", "orchestrator", "clarify_draft", "end_run"])
    g.add_edge("clarify_draft", "await_draft")
    g.add_edge("revise_draft", "enforce_length")
    g.add_edge("finalize", "send_final")
    g.add_edge("send_final", END)
    g.add_edge("end_run", END)
    return g.compile(checkpointer=checkpointer)
