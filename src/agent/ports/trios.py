"""TrioRetriever: Golden Bucket search (HLD 02, ADR 0005, ADR 0008)."""

from typing import Protocol

from agent.domain.identity import BrandScope
from agent.domain.trios import TrioSearchResult


class TrioRetriever(Protocol):
    """Interface:

    - One method, used by the ``retrieve_trios`` node and the ``search_golden_trios`` tool.
    - Returns only ``active`` Trios whose Brands are all inside ``scope`` (all-Brand Trios only
      to an all-Brands scope), with score at or above the adapter's threshold, best first, at
      most ``limit``.
    - Embeds ``query`` itself (through ``LLMFactory.embeddings``); callers pass text.
    - A failure raises ``TransientError(dependency="trios")``; the node then continues from the
      schema alone and flags the turn degraded. It never blocks a turn.
    - Adapters: ``NullTrioRetriever`` (always empty, if the slice is cut), SQLite + cosine in
      Python (prototype), pgvector + full-text with RRF (production).
    """

    def search(self, query: str, scope: BrandScope, *, limit: int = 3) -> TrioSearchResult: ...
