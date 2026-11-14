"""Application-state DDL.

- ``postgres.sql``: production schema (Cloud SQL + pgvector), the source of truth (ADR 0002).
- ``sqlite.sql``: the prototype's schema behind the same repository ports.
- ``ER.md``: the ER diagram.

The LangGraph checkpointer owns its own tables; they are not declared here.
"""

from importlib.resources import files


def ddl(dialect: str) -> str:
    """Return the DDL script for ``"postgres"`` or ``"sqlite"``."""
    if dialect not in ("postgres", "sqlite"):
        raise ValueError(f"unknown dialect: {dialect}")
    return (files(__package__) / f"{dialect}.sql").read_text()
