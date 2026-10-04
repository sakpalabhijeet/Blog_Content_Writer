"""Resolve image specs to files and place them into Markdown deterministically (no LLM echoing the post)."""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from . import providers

log = logging.getLogger(__name__)

TOP = "__top__"

# Provider chain per image kind; providers without keys fail fast and are skipped.
CHAINS = {
    "diagram": ["graphviz", "gemini"],
    "photo": ["pexels", "unsplash", "gemini"],
    "illustration": ["gemini", "pexels", "unsplash"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _heading_lines(lines: List[str]) -> List[int]:
    return [i for i, ln in enumerate(lines) if re.match(r"^##\s+", ln)]


def insert_block(md: str, after_heading: str, block: str) -> str:
    """Insert block after the first paragraph of the section titled `after_heading` (or under the H1 for TOP)."""
    lines = md.split("\n")
    if after_heading == TOP:
        idx = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1)
        return "\n".join(lines[: idx + 1] + ["", block, ""] + lines[idx + 1 :])

    heads = _heading_lines(lines)
    target = None
    want = _norm(after_heading)
    for i in heads:
        h = _norm(lines[i].lstrip("#"))
        if h == want or (want and (want in h or h in want)):
            target = i
            break
    if target is None:
        if not heads:
            return md.rstrip() + "\n\n" + block + "\n"
        target = heads[len(heads) // 2]

    # Skip to the end of the first paragraph after the heading (never inside a code fence).
    j, in_para, in_code = target + 1, False, False
    while j < len(lines):
        ln = lines[j]
        if ln.strip().startswith("```"):
            in_code = not in_code
        if not in_code and re.match(r"^#{1,6}\s", ln):
            break
        if ln.strip():
            in_para = True
        elif in_para and not in_code:
            break
        j += 1
    return "\n".join(lines[:j] + ["", block, ""] + lines[j:])


def image_markdown(rel_path: str, alt: str, caption: str) -> str:
    return f"![{alt}]({rel_path})\n*{caption}*"


def suggestion_block(spec: dict) -> str:
    return f"> 📷 **Suggested image ({spec.get('kind', 'image')}):** {spec.get('caption', '')} — _{spec.get('alt', '')}_"


def resolve_spec(spec: dict, images_dir: Path, fix_dot: Optional[Callable[[str, str], str]] = None) -> Optional[str]:
    """Produce the image file for a spec; return Markdown or None if every provider failed."""
    images_dir.mkdir(parents=True, exist_ok=True)
    out_path = images_dir / spec["filename"]
    credit = ""
    if not out_path.exists():
        errors = []
        for name in CHAINS.get(spec.get("kind", "photo"), CHAINS["photo"]):
            try:
                if name == "graphviz":
                    dot = spec.get("dot_source") or ""
                    if not dot:
                        raise RuntimeError("no DOT source")
                    try:
                        data, credit = providers.render_dot(dot)
                    except Exception as e:
                        if not fix_dot or "not found" in str(e):
                            raise
                        spec["dot_source"] = fix_dot(dot, str(e))
                        data, credit = providers.render_dot(spec["dot_source"])
                elif name in ("pexels", "unsplash"):
                    data, credit = getattr(providers, name)(spec.get("search_query") or spec.get("alt") or "technology")
                else:
                    data, credit = providers.gemini(spec.get("prompt") or spec.get("caption") or spec.get("alt"))
                out_path.write_bytes(data)
                break
            except Exception as e:
                errors.append(f"{name}: {e}")
        else:
            log.warning("image %s unresolved: %s", spec["filename"], "; ".join(errors))
            return None
    caption = spec.get("caption", "")
    if credit and credit not in caption:
        caption = f"{caption} ({credit})" if caption else credit
    return image_markdown(f"{images_dir.name}/{out_path.name}", spec.get("alt", ""), caption)


def place_auto(md: str, specs: List[dict], images_dir: Path, fix_dot=None) -> str:
    for spec in specs:
        block = resolve_spec(spec, images_dir, fix_dot) or suggestion_block(spec)
        md = insert_block(md, spec.get("insert_after_heading") or TOP, block)
    return md


def place_suggestions(md: str, specs: List[dict]) -> str:
    for spec in specs:
        md = insert_block(md, spec.get("insert_after_heading") or TOP, suggestion_block(spec))
    return md


def place_user_images(md: str, image_paths: List[str], images_dir: Path) -> str:
    """First user image becomes the hero; the rest are spread evenly across sections."""
    images_dir.mkdir(parents=True, exist_ok=True)
    lines = md.split("\n")
    headings = [lines[i].lstrip("#").strip() for i in _heading_lines(lines)]
    placements = []
    for k, src in enumerate(image_paths):
        src_p = Path(src)
        dest = images_dir / f"user_{k + 1}{src_p.suffix.lower()}"
        if src_p.resolve() != dest.resolve():
            shutil.copyfile(src_p, dest)
        if k == 0 or not headings:
            where = TOP
        else:
            rest = len(image_paths) - 1
            where = headings[min(len(headings) - 1, (k - 1) * len(headings) // max(rest, 1))]
        alt = re.sub(r"[_-]+", " ", src_p.stem)
        placements.append((where, image_markdown(f"{images_dir.name}/{dest.name}", alt, alt)))
    for where, block in placements:
        md = insert_block(md, where, block)
    return md
