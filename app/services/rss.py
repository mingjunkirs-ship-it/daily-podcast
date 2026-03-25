from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from app.services.types import NormalizedItem


def _rfc2822(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def build_rss_xml(
    feed_title: str,
    feed_link: str,
    feed_description: str,
    items: list[NormalizedItem],
) -> str:
    item_nodes: list[str] = []

    for item in items:
        title = escape(item.title or "Untitled")
        link = escape(item.link or "")
        description = escape((item.summary or item.content or "")[:2000])
        pub_date = _rfc2822(item.published_at)
        guid = escape(item.unique_key())
        author = escape(item.author) if item.author else ""

        author_node = f"<author>{author}</author>" if author else ""
        item_nodes.append(
            "\n".join(
                [
                    "<item>",
                    f"<title>{title}</title>",
                    f"<link>{link}</link>",
                    f"<guid>{guid}</guid>",
                    f"<pubDate>{pub_date}</pubDate>",
                    f"<description>{description}</description>",
                    author_node,
                    "</item>",
                ]
            )
        )

    now = _rfc2822(datetime.now(timezone.utc))
    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0">',
            "<channel>",
            f"<title>{escape(feed_title)}</title>",
            f"<link>{escape(feed_link)}</link>",
            f"<description>{escape(feed_description)}</description>",
            f"<lastBuildDate>{now}</lastBuildDate>",
            *item_nodes,
            "</channel>",
            "</rss>",
        ]
    )
    return xml

