"""Adapters that stand in for a slice not built yet, honestly: they do nothing, visibly."""

from agent.domain.identity import BrandScope
from agent.domain.trios import TrioSearchResult
from agent.ports.pii import PiiScan


class NullTrioRetriever:
    """The Golden Bucket is empty (ADR 0008 keeps retrieval a fixed step even then)."""

    def search(self, query: str, scope: BrandScope, *, limit: int = 3) -> TrioSearchResult:
        return TrioSearchResult(matches=[], dropped_below_threshold=0)


class PassThroughPiiFilter:
    """Placeholder until the regex detectors land in slice 2. ``Outbound.emit`` already calls
    the port, so replacing this adapter is one line in ``composition``."""

    def scan(self, text: str, *, templated: bool = False) -> PiiScan:
        return PiiScan(text=text, hits=[], detectors="none")
