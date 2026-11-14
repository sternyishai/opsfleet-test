"""QueryGuard and QueryRunner: the path from model-written SQL to BigQuery (HLD 03, HLD 06)."""

from typing import Any, Protocol

from agent.domain.identity import BrandScope
from agent.domain.query import DryRunResult, GuardDecision, QueryResult


class QueryGuard(Protocol):
    """Parse, verify and rewrite one SQL statement for one scope. Pure: no I/O, no clock.

    Interface:

    - **Fails closed.** Anything it cannot parse or verify is ``GuardRejected``; it never raises
      for bad SQL.
    - Allowed SQL is exactly one ``SELECT`` (CTEs allowed) on ``orders``, ``order_items``,
      ``products``, ``users``, non-PII columns only.
    - A Brand literal outside ``scope`` is rejected with ``BRAND_OUT_OF_SCOPE``, never filtered.
    - Every table reference is replaced by a scoped subquery; Brands are bound as the query
      parameter ``@brands``, never concatenated. For a Manager, ``orders.num_of_item`` is not
      projected, and a reference to it is rejected with ``ORDER_LEVEL_FIELD`` and a hint.
    - A ``LIMIT`` of at most ``row_limit`` is forced on the outer query.
    - An empty scope (no role) rejects everything with ``NO_ROLE``.
    - The dialect is a constructor setting of the adapter (``bigquery`` today).
    """

    def check(self, sql: str, scope: BrandScope, *, row_limit: int) -> GuardDecision: ...


class QueryRunner(Protocol):
    """Run already-guarded SQL on the warehouse. Only ``run_sql`` and its probe call this.

    Interface:

    - Takes only SQL that came out of ``QueryGuard.check`` (``GuardAllowed.rewritten_sql`` and
      its ``params``). It does not re-check; the guard is the gate.
    - Read-only identity; job timeout on every job, so an abandoned query stops by itself.
    - Raises ``SelfCorrectableError`` for syntax, schema and runtime errors (text cleaned of
      service names), and ``TransientError(dependency="bigquery", DATA_SOURCE_DOWN)`` once its
      retries and circuit breaker give up. Dry runs and queries are idempotent, so the adapter
      retries them itself.
    """

    def dry_run(self, sql: str, params: dict[str, Any], *, max_bytes: int) -> DryRunResult:
        """Free. Catches syntax and schema errors; reports the bytes the query would scan."""
        ...

    def execute(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, max_rows: int
    ) -> QueryResult:
        """Run with ``maximum_bytes_billed = max_bytes``; return at most ``max_rows`` rows."""
        ...
