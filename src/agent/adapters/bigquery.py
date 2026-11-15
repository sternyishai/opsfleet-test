"""QueryRunner on BigQuery with application-default credentials.

Unqualified table names in guarded SQL resolve against ``default_dataset``, so the guard and
the model never spell the project. Every job carries ``maximum_bytes_billed`` and a job
timeout, so an abandoned query stops by itself. Retries and the breaker arrive in slice 3.
"""

from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import bigquery

from agent.domain.query import DryRunResult, QueryResult
from agent.errors import AgentError, MessageClass, SelfCorrectableError, TransientError

_RETRYABLE = (
    gexc.TooManyRequests,
    gexc.ServerError,
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    TimeoutError,
    ConnectionError,
)


class BigQueryRunner:
    def __init__(self, *, project: str | None, dataset: str, timeout_s: float) -> None:
        self._client = bigquery.Client(project=project)
        self._dataset = bigquery.DatasetReference.from_string(dataset)
        self._timeout_s = timeout_s

    def dry_run(self, sql: str, params: dict[str, Any], *, max_bytes: int) -> DryRunResult:
        job = self._run(sql, params, max_bytes=max_bytes, dry_run=True)
        processed = job.total_bytes_processed or 0
        return DryRunResult(bytes_processed=processed, over_cap=processed > max_bytes)

    def execute(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, max_rows: int
    ) -> QueryResult:
        job = self._run(sql, params, max_bytes=max_bytes, dry_run=False)
        try:
            rows = job.result(timeout=self._timeout_s, max_results=max_rows)
            columns = [field.name for field in rows.schema]
            records = [dict(row.items()) for row in rows]
        except gexc.GoogleAPICallError as exc:
            raise _classify(exc) from exc
        except _RETRYABLE as exc:
            raise _down(exc) from exc
        total = rows.total_rows or len(records)
        return QueryResult(
            columns=columns,
            rows=records,
            row_count=total,
            truncated=total > len(records),
            bytes_billed=job.total_bytes_billed or 0,
        )

    def _run(
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
        try:
            return self._client.query(sql, job_config=config)
        except gexc.GoogleAPICallError as exc:
            raise _classify(exc) from exc
        except _RETRYABLE as exc:
            raise _down(exc) from exc


def _param(name: str, value: Any) -> bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter:
    if isinstance(value, list | tuple | set | frozenset):
        return bigquery.ArrayQueryParameter(name, "STRING", sorted(value))
    kind = {bool: "BOOL", int: "INT64", float: "FLOAT64"}.get(type(value), "STRING")
    return bigquery.ScalarQueryParameter(name, kind, value)


def _classify(exc: gexc.GoogleAPICallError) -> AgentError:
    if isinstance(exc, gexc.BadRequest):  # syntax, schema, runtime, bytes cap
        return SelfCorrectableError(_clean(exc))
    if isinstance(exc, _RETRYABLE):
        return _down(exc)
    return AgentError(_clean(exc))  # permission and the like: unclassified until slice 3


def _down(exc: Exception) -> TransientError:
    return TransientError(
        type(exc).__name__, dependency="bigquery", message_class=MessageClass.DATA_SOURCE_DOWN
    )


def _clean(exc: gexc.GoogleAPICallError) -> str:
    """The first error message, without the job URL and service name the model needn't see."""
    errors = getattr(exc, "errors", None) or []
    message = errors[0].get("message") if errors else exc.message
    return str(message).replace("bigquery-public-data.", "").strip()[:1000]
