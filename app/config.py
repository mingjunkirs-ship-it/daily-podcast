from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
AUDIO_DIR = DATA_DIR / "audio"
FEEDS_DIR = DATA_DIR / "feeds"
NOTES_DIR = DATA_DIR / "notes"

for directory in (DATA_DIR, AUDIO_DIR, FEEDS_DIR, NOTES_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'podcast.db').as_posix()}")

