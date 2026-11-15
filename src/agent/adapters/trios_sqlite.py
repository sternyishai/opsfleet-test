"""TrioRetriever over SQLite, with cosine similarity in Python (prototype, HLD 02, LLD 06).

The seed Trios in ``seed/trios/`` are synced into ``golden_trios`` on construction (no network).
Their vectors (one per question and per paraphrase, ``trio_embeddings``) are made on the first
search that needs them and kept in the file, so a restart embeds only queries. The table is
kept equal to the seed (``_sync``).

Five Trios are a few dozen vectors: a scan in Python is fast enough, and the production adapter
(pgvector + full text, RRF) keeps the same filter and threshold.
"""

import json
import math
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from langchain_core.embeddings import Embeddings

from agent.db import EMBEDDING_DIMENSIONS, ddl
from agent.domain.identity import BrandScope
from agent.domain.trios import Trio, TrioMatch, TrioParameter, TrioSearchResult, TrioStatus
from agent.errors import TransientError

_COLUMNS = (
    "trio_id",
    "version",
    "question",
    "paraphrases",
    "sql",
    "method_summary",
    "parameters",
    "scope_all",
    "brands",
    "tables_used",
    "question_type",
    "status",
    "source_uri",
    "approved_by",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM golden_trios"

type TrioKey = tuple[str, int]
"""``(trio_id, version)``, the table's primary key."""


class SqliteTrioRetriever:
    def __init__(
        self, db_path: Path, *, seed: Path, embeddings: Embeddings, min_score: float
    ) -> None:
        self._db_path = db_path
        self._embeddings = embeddings
        self._min_score = min_score
        db_path.parent.mkdir(parents=True, exist_ok=True)
        trios = [Trio.model_validate(yaml.safe_load(p.read_text())) for p in seed.glob("*.yaml")]
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'golden_trios'").fetchone():
                db.executescript(ddl("sqlite"))
            _sync(db, trios)

    def search(self, query: str, scope: BrandScope, *, limit: int = 3) -> TrioSearchResult:
        try:
            with self._connect() as db:
                self._embed_missing(db)
                trios = [t for t in _active(db) if _reachable(t, scope)]
                vectors = _vectors(db)
            asked = self._embeddings.embed_query(query)
            scored = [(_best(asked, vectors.get((t.trio_id, t.version), [])), t) for t in trios]
        except Exception as exc:  # embedding model or app DB: retrieval never blocks a turn
            raise TransientError(type(exc).__name__, dependency="trios") from exc
        kept = sorted(((s, t) for s, t in scored if s >= self._min_score), key=lambda p: -p[0])
        return TrioSearchResult(
            matches=[_match(t, s) for s, t in kept[: max(limit, 0)]],
            dropped_below_threshold=len(scored) - len(kept),
        )

    def _embed_missing(self, db: sqlite3.Connection) -> None:
        """Vectors for every Trio that has none yet, in one embedding call."""
        texts = [
            (trio_id, version, ordinal, text)
            for trio_id, version, question, paraphrases in db.execute(
                "SELECT trio_id, version, question, paraphrases FROM golden_trios t "
                "WHERE NOT EXISTS (SELECT 1 FROM trio_embeddings e "
                "WHERE e.trio_id = t.trio_id AND e.version = t.version)"
            ).fetchall()
            for ordinal, text in enumerate([question, *json.loads(paraphrases)])
        ]
        if not texts:
            return
        vectors = self._embeddings.embed_documents([t[3] for t in texts])
        db.executemany(
            "INSERT INTO trio_embeddings (trio_id, version, ordinal, text, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            [(*t, _pack(v)) for t, v in zip(texts, vectors, strict=True)],
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()


def _sync(db: sqlite3.Connection, trios: list[Trio]) -> None:
    """Make the table hold exactly the seed: in the prototype the seed is the whole Golden
    Bucket. An unchanged Trio is left alone (its vectors stay); a changed one is rewritten (the
    cascade drops its vectors); a new active version supersedes the older one; a Trio whose
    file was deleted is deleted."""
    seeded = {(t.trio_id, t.version) for t in trios}
    for row in db.execute("SELECT trio_id, version FROM golden_trios").fetchall():
        if (row["trio_id"], row["version"]) not in seeded:
            db.execute("DELETE FROM golden_trios WHERE trio_id = ? AND version = ?", tuple(row))
    for trio in trios:
        row = _row(trio)
        stored = db.execute(
            f"{_SELECT} WHERE trio_id = ? AND version = ?", (trio.trio_id, trio.version)
        ).fetchone()
        if stored is not None and dict(stored) == row:
            continue
        db.execute(
            "DELETE FROM golden_trios WHERE trio_id = ? AND version = ?",
            (trio.trio_id, trio.version),
        )
        if trio.status is TrioStatus.ACTIVE:  # at most one active version (partial index)
            db.execute(
                "UPDATE golden_trios SET status = ? WHERE trio_id = ? AND status = ?",
                (TrioStatus.SUPERSEDED.value, trio.trio_id, TrioStatus.ACTIVE.value),
            )
        db.execute(
            f"INSERT INTO golden_trios ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )


def _row(trio: Trio) -> dict[str, object]:
    values: tuple[object, ...] = (
        trio.trio_id,
        trio.version,
        trio.question,
        json.dumps(trio.paraphrases),
        trio.sql,
        trio.method_summary,
        json.dumps([p.model_dump(mode="json") for p in trio.parameters]),
        int(trio.scope.all_brands),
        json.dumps(sorted(trio.scope.brands)),
        json.dumps(trio.tables_used),
        trio.question_type.value,
        trio.status.value,
        trio.source_uri,
        trio.approved_by,
    )
    return dict(zip(_COLUMNS, values, strict=True))


def _active(db: sqlite3.Connection) -> list[Trio]:
    rows = db.execute(f"{_SELECT} WHERE status = ?", (TrioStatus.ACTIVE.value,)).fetchall()
    return [
        Trio(
            trio_id=r["trio_id"],
            version=r["version"],
            question=r["question"],
            paraphrases=json.loads(r["paraphrases"]),
            sql=r["sql"],
            method_summary=r["method_summary"],
            parameters=[TrioParameter.model_validate(p) for p in json.loads(r["parameters"])],
            scope=BrandScope(
                all_brands=bool(r["scope_all"]), brands=frozenset(json.loads(r["brands"]))
            ),
            tables_used=json.loads(r["tables_used"]),
            question_type=r["question_type"],
            status=r["status"],
            source_uri=r["source_uri"],
            approved_by=r["approved_by"],
        )
        for r in rows
    ]


def _reachable(trio: Trio, scope: BrandScope) -> bool:
    """Fully inside the scope. A Trio tagged with no Brands is read as untagged, so it goes to
    Executives only: an empty set is inside every Manager's scope, which would default open."""
    if not trio.scope.all_brands and not trio.scope.brands:
        return scope.all_brands
    return scope.covers(trio.scope)


def _vectors(db: sqlite3.Connection) -> dict[TrioKey, list[list[float]]]:
    out: dict[TrioKey, list[list[float]]] = {}
    for row in db.execute("SELECT trio_id, version, embedding FROM trio_embeddings").fetchall():
        out.setdefault((row["trio_id"], row["version"]), []).append(_unpack(row["embedding"]))
    return out


def _best(query: list[float], vectors: list[list[float]]) -> float:
    """The best match over the question and its paraphrases; one score per Trio."""
    return max((_cosine(query, v) for v in vectors), default=0.0)


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if not norm:
        return 0.0
    # Clamped: an opposite vector is no match, and TrioMatch.score is in [0, 1].
    return min(1.0, max(0.0, sum(x * y for x, y in zip(a, b, strict=True)) / norm))


def _pack(vector: list[float]) -> bytes:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}")
    return struct.pack(f"<{EMBEDDING_DIMENSIONS}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{EMBEDDING_DIMENSIONS}f", blob))


def _match(trio: Trio, score: float) -> TrioMatch:
    return TrioMatch(
        ref=trio.ref,
        score=score,
        question=trio.question,
        method_summary=trio.method_summary,
        sql=trio.sql,
        parameters=trio.parameters,
        tables_used=trio.tables_used,
    )
