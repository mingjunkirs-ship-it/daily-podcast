from __future__ import annotations

from typing import Any


RSSHUB_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "github-releases",
        "title": "GitHub Releases",
        "route": "/github/repo/DIYgod/RSSHub/releases",
        "note": "仓库发布更新",
    },
    {
        "key": "github-issues",
        "title": "GitHub Issues",
        "route": "/github/issue/DIYgod/RSSHub",
        "note": "仓库 Issue 动态",
    },
    {
        "key": "reddit-subreddit",
        "title": "Reddit Subreddit",
        "route": "/reddit/r/LocalLLaMA",
        "note": "替换为你的 subreddit",
    },
    {
        "key": "youtube-channel",
        "title": "YouTube Channel",
        "route": "/youtube/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
        "note": "替换为频道 ID",
    },
    {
        "key": "weibo-user",
        "title": "微博用户",
        "route": "/weibo/user/1195230310",
        "note": "替换为用户 UID",
    },
]


def list_rsshub_templates() -> list[dict[str, Any]]:
    return RSSHUB_TEMPLATES

