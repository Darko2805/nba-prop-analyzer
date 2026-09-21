"""
Blog posts, sourced from plain Markdown files in content/blog/ rather than
a database -- publishing a post is "add a .md file, commit, deploy," no
admin UI or CMS needed. Fits the content-calendar workflow (posts get
written ahead of time and shipped on a schedule) better than a database
would for a single-author blog with no reader interaction (comments,
likes) planned.

Each file's first lines are simple `key: value` front matter (title,
date, summary), ended by a line of exactly `---`, followed by the post
body in Markdown. Filename (minus .md) is the slug and the URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import markdown as _markdown

_CONTENT_DIR = Path(__file__).resolve().parent.parent.parent / "content" / "blog"


@dataclass
class BlogPost:
    slug: str
    title: str
    date: str
    summary: str
    html: str = ""


def _parse_front_matter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    meta = {}
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    body = "\n".join(lines[body_start:])
    return meta, body


def _load(path: Path, render_body: bool) -> BlogPost:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_front_matter(text)
    return BlogPost(
        slug=path.stem,
        title=meta.get("title", path.stem),
        date=meta.get("date", ""),
        summary=meta.get("summary", ""),
        html=_markdown.markdown(body, extensions=["extra"]) if render_body else "",
    )


def list_posts() -> list[BlogPost]:
    """Newest first, by the front-matter date string (ISO dates sort correctly as text)."""
    if not _CONTENT_DIR.is_dir():
        return []
    posts = [_load(p, render_body=False) for p in _CONTENT_DIR.glob("*.md")]
    return sorted(posts, key=lambda p: p.date, reverse=True)


def get_post(slug: str) -> BlogPost | None:
    # Guard against path traversal since slug comes straight from the URL.
    if "/" in slug or "\\" in slug or slug in (".", ".."):
        return None
    path = _CONTENT_DIR / f"{slug}.md"
    if not path.is_file():
        return None
    return _load(path, render_body=True)
