"""Central configuration for the Search Typeahead system.

Every tunable value lives here so there are no "magic numbers" scattered around
the codebase. Values come from environment variables (see .env.example); each
has a sensible default that matches docker-compose, so the app runs out of the
box without any .env file.

We intentionally avoid a heavyweight settings library -- a plain dataclass plus
os.getenv is easy to read and easy to explain in a viva.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: copy KEY=VALUE lines into os.environ if not already
    set. We hand-roll this (instead of adding python-dotenv) to keep the
    dependency list tiny and the behaviour obvious."""
    env_file = Path(path)
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    # --- PostgreSQL ---
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://typeahead:typeahead@localhost:5433/typeahead",
    )

    # --- Redis logical cache nodes ---
    # Parsed from a comma-separated "host:port,host:port" string.
    redis_nodes: list[str] = field(
        default_factory=lambda: [
            node.strip()
            for node in os.getenv(
                "REDIS_NODES", "localhost:6391,localhost:6392,localhost:6393"
            ).split(",")
            if node.strip()
        ]
    )

    # --- Cache behaviour ---
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "30"))
    virtual_nodes: int = int(os.getenv("VIRTUAL_NODES", "150"))

    # --- Suggestions ---
    suggestion_limit: int = int(os.getenv("SUGGESTION_LIMIT", "10"))
    max_prefix_len: int = int(os.getenv("MAX_PREFIX_LEN", "12"))

    # --- Trending / recency-aware ranking ---
    default_ranking_mode: str = os.getenv("DEFAULT_RANKING_MODE", "trending")
    recency_alpha: float = float(os.getenv("RECENCY_ALPHA", "2000.0"))
    recency_halflife_seconds: float = float(
        os.getenv("RECENCY_HALFLIFE_SECONDS", "600")
    )

    # --- Batch writes ---
    batch_size: int = int(os.getenv("BATCH_SIZE", "500"))
    flush_interval_seconds: float = float(os.getenv("FLUSH_INTERVAL_SECONDS", "2.0"))


# A single shared instance imported everywhere: `from app.config import settings`.
settings = Settings()
