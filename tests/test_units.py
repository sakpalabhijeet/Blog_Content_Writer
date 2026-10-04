from __future__ import annotations


import pytest

from content_agents.images.pipeline import TOP, insert_block, place_user_images
from content_agents.mail.parse import parse_reply, strip_quoted
from content_agents.mail.send import OutgoingEmail, build_message
from content_agents.mail.templates import md_to_html
from content_agents.text_utils import devanagari_ratio, extract_links, word_count


@pytest.mark.parametrize("text,stage,action,choice", [
    ("2", "topics", "choose", 2),
    ("Topic 3 please, focus on Python", "topics", "choose", 3),
    ("approve 1", "topics", "choose", 1),
    ("#1\nIMAGES: MANUAL", "topics", "choose", 1),
    ("7", "topics", "unclear", None),
    ("REGENERATE: more about agents", "topics", "regenerate", None),
    ("reject", "topics", "regenerate", None),
    ("STOP", "topics", "stop", None),
    ("APPROVE", "draft", "approve", None),
    ("Looks good!", "draft", "approve", None),
    ("revise: shorter intro\nand add code", "draft", "revise", None),
    ("Re-evaluate the conclusion", "draft", "revise", None),
    ("regenerate - totally different angle", "draft", "regenerate", None),
    ("REJECT", "draft", "reject", None),
    ("cancel", "draft", "stop", None),
])
def test_parse_reply(text, stage, action, choice):
    r = parse_reply(text, stage, 3, use_llm=False)
    assert r.action == action
    assert r.choice == choice


def test_parse_feedback_and_directives():
    r = parse_reply("revise: shorter intro\nand add code\nIMAGES: none\n\nOn Tue, 23 Sep 2026, Bot <x@y>\nwrote:\n> old",
                    "draft", use_llm=False)
    assert r.feedback == "shorter intro\nand add code"
    assert r.images_mode == "none"
    r = parse_reply("1\nLANG: english", "topics", use_llm=False)
    assert r.language == "english"


def test_strip_quoted():
    assert strip_quoted("2\n\n> previous\n> email") == "2"
    assert strip_quoted("APPROVE\n\nOn Mon, Sep 22, 2026 at 9:00 AM Agent <a@b.c> wrote:\n> x") == "APPROVE"
    assert strip_quoted("ok\n-----Original Message-----\nFrom: x") == "ok"


def test_word_count_ignores_code_and_images():
    md = "# Title\n\nHello world [link](https://x.y/z)\n\n```python\nprint('a b c d')\n```\n![alt text](images/a.png)"
    assert word_count(md) == 4  # Title Hello world link


def test_devanagari_ratio():
    assert devanagari_ratio("नमस्ते दुनिया") > 0.9
    assert devanagari_ratio("hello world") == 0
    assert 0.3 < devanagari_ratio("नमस्ते hello") < 0.8


def test_extract_links_dedupes():
    assert extract_links("[a](https://a.com) [b](https://a.com) [c](https://c.com)") == [("a", "https://a.com"), ("c", "https://c.com")]


def test_insert_block_after_first_paragraph():
    md = "# T\n\n## Intro\n\npara one\nline two\n\npara two\n\n## Next\n\nx"
    out = insert_block(md, "intro", "IMG")
    assert out.index("IMG") > out.index("line two") and out.index("IMG") < out.index("para two")
    out = insert_block(md, TOP, "HERO")
    assert out.split("\n")[2] == "HERO"
    out = insert_block(md, "Does not exist", "IMG")  # falls back to a middle section
    assert "IMG" in out


def test_insert_block_skips_code_fences():
    md = "## Code\n\n```\nline\n\nmore\n```\n\nafter\n"
    out = insert_block(md, "Code", "IMG")
    assert out.index("IMG") > out.index("more")


def test_place_user_images(tmp_path):
    imgs = []
    for n in ("hero.png", "b.jpg"):
        p = tmp_path / n
        p.write_bytes(b"x")
        imgs.append(str(p))
    md = "# T\n\nintro\n\n## A\n\na\n\n## B\n\nb\n"
    out = place_user_images(md, imgs, tmp_path / "images")
    assert "images/user_1.png" in out and "images/user_2.jpg" in out
    assert out.index("user_1") < out.index("## A")


def test_email_inline_images(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"\x89PNG....")
    html, inline = md_to_html("# T\n\n![alt](images/a.png)", tmp_path)
    assert len(inline) == 1 and "cid:" in html
    msg = build_message(OutgoingEmail("run-x", "[CA-run-x#1] subject", html, "text", [], inline))
    raw = bytes(msg)
    assert b"Content-ID" in raw and b"[CA-run-x#1]" in raw


def test_hindi_word_count_and_narration():
    from content_agents.text_utils import narration

    assert word_count("नमस्ते दुनिया आप कैसे हैं") == 5
    script = "## HOOK\n[VISUAL: dark forest]\nनमस्ते दोस्तों\n(pause)\n**सच**"
    text = narration(script)
    assert "VISUAL" not in text and "pause" not in text and "HOOK" in text and "##" not in text
    assert devanagari_ratio(narration("[VISUAL: a very long english cue here]\nनमस्ते")) > 0.9
