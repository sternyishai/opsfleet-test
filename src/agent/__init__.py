"""Retail Analysis Assistant.

Package layout (see docs/lld/01-modules.md):

- ``domain``     records the system stores and passes around (Pydantic)
- ``contracts``  router output, channel messages and every tool's input/output
- ``ports``      ``Protocol`` seams to every external dependency (ADR 0006)
- ``graph``      LangGraph state and the node map
- ``tools``      the eleven tool functions (stubs)
- ``core``       deep modules with no external dependency of their own:
                 the single outbound exit, the confirmation parser, the Persona Quality Gate
- ``db``         SQL DDL (Postgres for production, SQLite for the prototype) and the ER diagram
- ``adapters``   concrete adapters, the only place provider SDKs may be imported
- ``composition`` the single composition root
"""

__version__ = "0.1.0"
