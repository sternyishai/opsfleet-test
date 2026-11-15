"""QueryRunner on BigQuery with application-default credentials.

Unqualified table names in guarded SQL resolve against ``default_dataset``, so the guard and
the model never spell the project. Every job carries ``maximum_bytes_billed`` and a job
timeout, so an abandoned query stops by itself.

Resilience (HLD 06): dry runs and queries are idempotent, so each whole call (submit and
wait) is retried here on 429, 5xx and timeouts, at most ``retry_attempts`` times, behind one
circuit breaker for BigQuery. The client's own retries are turned off (its job retry would
wait up to 40 minutes), so these are the only ones. What comes out:

- ``BadRequest`` (syntax, schema, runtime error, bytes cap): ``SelfCorrectableError``, the
  first message cleaned of the dataset path; never retried.
- retryable errors once retries are spent, or the breaker open: ``TransientError(bigquery,
  DATA_SOURCE_DOWN)``.
- anything else from the API (permission, not found): ``PermanentError(DATA_SOURCE_DOWN)``.
"""

import time
from collections.abc import Callable
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import bigquery

from agent.adapters.resilience import CircuitBreaker, Clock, Retry, Sleep, Unavailable
from agent.config import Limits
from agent.domain.query import DryRunResult, QueryResult
from agent.errors import (
    AgentError,
    MessageClass,
    PermanentError,
    SelfCorrectableError,
    TransientError,
)

_RETRYABLE = (
    gexc.TooManyRequests,
    gexc.ServerError,
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    TimeoutError,
    ConnectionError,
)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE):
        return True
    # BigQuery reports its own rate limits as 403 with reason rateLimitExceeded.
    return isinstance(exc, gexc.Forbidden) and any(
        e.get("reason") == "rateLimitExceeded" for e in (exc.errors or [])
    )


class BigQueryRunner:
    def __init__(
        self,
        *,
        project: str | None,
        dataset: str,
        limits: Limits,
        client: Any = None,
        sleep: Sleep = time.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._client = client or bigquery.Client(project=project)
        self._dataset = bigquery.DatasetReference.from_string(dataset)
        self._timeout_s = limits.timeout_bigquery_s
        self._retry = Retry(
            attempts=limits.retry_attempts,
            max_wait_s=limits.retry_max_wait_s,
            is_retryable=_retryable,
            retry_after=lambda _: None,
            sleep=sleep,
        )
        self._breaker = CircuitBreaker(
            failures=limits.breaker_failures,
            window_s=limits.breaker_window_s,
            cooldown_s=limits.breaker_cooldown_s,
            clock=clock,
        )

    def dry_run(self, sql: str, params: dict[str, Any], *, max_bytes: int) -> DryRunResult:
        def run() -> DryRunResult:
            job = self._submit(sql, params, max_bytes=max_bytes, dry_run=True)
            processed = job.total_bytes_processed or 0
            return DryRunResult(bytes_processed=processed, over_cap=processed > max_bytes)

        return self._resilient(run)

    def execute(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, max_rows: int
    ) -> QueryResult:
        def run() -> QueryResult:
            job = self._submit(sql, params, max_bytes=max_bytes, dry_run=False)
            rows = job.result(timeout=self._timeout_s, max_results=max_rows, job_retry=None)
            records = [dict(row.items()) for row in rows]
            total = rows.total_rows or len(records)
            return QueryResult(
                columns=[field.name for field in rows.schema],
                rows=records,
                row_count=total,
                truncated=total > len(records),
                bytes_billed=job.total_bytes_billed or 0,
            )

        return self._resilient(run)

    def _submit(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, dry_run: bool
    ) -> bigquery.QueryJob:
        config = bigquery.QueryJobConfig(
            default_dataset=self._dataset,
            maximum_bytes_billed=max_bytes,
            query_parameters=[_param(k, v) for k, v in params.items()],
            dry_run=dry_run,
            use_query_cache=not dry_run,
            job_timeout_ms=int(self._timeout_s * 1000),
        )
        # retry=None: the client accepts it (no transport retry) though its stub says Retry.
        return self._client.query(
            sql,
            job_config=config,
            timeout=self._timeout_s,
            retry=None,  # pyright: ignore[reportArgumentType]
            job_retry=None,
        )

    def _resilient[T](self, run: Callable[[], T]) -> T:
        try:
            result, _attempts = self._retry.call(run, self._breaker)
        except Unavailable as exc:
            raise _down(exc) from exc
        except Exception as exc:
            raise _classify(exc) from exc
        return result


def _param(name: str, value: Any) -> bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter:
    if isinstance(value, list | tuple | set | frozenset):
        return bigquery.ArrayQueryParameter(name, "STRING", sorted(value))
    kind = {bool: "BOOL", int: "INT64", float: "FLOAT64"}.get(type(value), "STRING")
    return bigquery.ScalarQueryParameter(name, kind, value)


def _classify(exc: Exception) -> AgentError:
    if isinstance(exc, gexc.BadRequest):  # syntax, schema, runtime, bytes cap
        return SelfCorrectableError(_clean(exc))
    if _retryable(exc):  # retries spent
        return _down(exc)
    if isinstance(exc, gexc.GoogleAPICallError):  # permission, not found: a setup problem
        return PermanentError(_clean(exc), message_class=MessageClass.DATA_SOURCE_DOWN)
    return AgentError(f"{type(exc).__name__}: {exc}"[:500])


def _down(exc: Exception) -> TransientError:
    return TransientError(
        type(exc).__name__, dependency="bigquery", message_class=MessageClass.DATA_SOURCE_DOWN
    )


def _clean(exc: gexc.GoogleAPICallError) -> str:
    """The first error message, without the job URL and service name the model needn't see."""
    errors = getattr(exc, "errors", None) or []
    message = errors[0].get("message") if errors else exc.message
    return str(message).replace("bigquery-public-data.", "").strip()[:1000]
