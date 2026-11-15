"""TrioRetriever over SQLite, with cosine similarity in Python (prototype, HLD 02, LLD 06).

The seed Trios in ``seed/trios/`` are synced into ``golden_trios`` on construction (no network).
Their vectors (one per question and per paraphrase, ``trio_embeddings``) are made on the first
search that needs them and kept in the file, so a restart embeds only queries. A seed Trio that
changed is rewritten, and the cascade drops its old vectors.

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
    "trio_id, version, question, paraphrases, sql, method_summary, parameters, scope_all, "
    "brands, tables_used, question_type, status, source_uri, approved_by"
)


class SqliteTrioRetriever:
    def __init__(
        self, db_path: Path, *, seed: Path, embeddings: Embeddings, min_score: float
    ) -> None:
        self._db_path = db_path
        self._embeddings = embeddings
        self._min_score = min_score
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'golden_trios'").fetchone():
                db.executescript(ddl("sqlite"))
            for path in sorted(seed.glob("*.yaml")):
                _sync(db, Trio.model_validate(yaml.safe_load(path.read_text())))

    def search(self, query: str, scope: BrandScope, *, limit: int = 3) -> TrioSearchResult:
        try:
            with self._connect() as db:
                self._embed_missing(db)
                trios = [t for t in _active(db) if scope.covers(t.scope)]
                vectors = _vectors(db, [t.ref for t in trios])
            asked = self._embeddings.embed_query(query)
        except Exception as exc:  # embedding model or app DB: retrieval never blocks a turn
            raise TransientError(type(exc).__name__, dependency="trios") from exc
        scored = [(_best(asked, vectors.get(t.ref, [])), t) for t in trios]
        kept = sorted(((s, t) for s, t in scored if s >= self._min_score), key=lambda p: -p[0])
        return TrioSearchResult(
            matches=[_match(t, s) for s, t in kept[:limit]],
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
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()


def _sync(db: sqlite3.Connection, trio: Trio) -> None:
    """Insert a seed Trio, or rewrite it when its content changed (dropping its vectors)."""
    row = _row(trio)
    stored = db.execute(
        f"SELECT {_COLUMNS} FROM golden_trios WHERE trio_id = ? AND version = ?",
        (trio.trio_id, trio.version),
    ).fetchone()
    if stored == row:
        return
    db.execute(
        "DELETE FROM golden_trios WHERE trio_id = ? AND version = ?", (trio.trio_id, trio.version)
    )
    db.execute(f"INSERT INTO golden_trios ({_COLUMNS}) VALUES ({', '.join('?' * len(row))})", row)


def _row(trio: Trio) -> tuple[object, ...]:
    return (
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


def _active(db: sqlite3.Connection) -> list[Trio]:
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM golden_trios WHERE status = ?", (TrioStatus.ACTIVE.value,)
    ).fetchall()
    return [
        Trio(
            trio_id=r[0],
            version=r[1],
            question=r[2],
            paraphrases=json.loads(r[3]),
            sql=r[4],
            method_summary=r[5],
            parameters=[TrioParameter.model_validate(p) for p in json.loads(r[6])],
            scope=BrandScope(all_brands=bool(r[7]), brands=frozenset(json.loads(r[8]))),
            tables_used=json.loads(r[9]),
            question_type=r[10],
            status=r[11],
            source_uri=r[12],
            approved_by=r[13],
        )
        for r in rows
    ]


def _vectors(db: sqlite3.Connection, refs: list[str]) -> dict[str, list[list[float]]]:
    """Each Trio's vectors by ``trio_id@version``."""
    out: dict[str, list[list[float]]] = {ref: [] for ref in refs}
    for trio_id, version, blob in db.execute(
        "SELECT trio_id, version, embedding FROM trio_embeddings"
    ).fetchall():
        ref = f"{trio_id}@{version}"
        if ref in out:
            out[ref].append(_unpack(blob))
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
