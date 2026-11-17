# Postgres for all application state

All application state (conversation checkpoints, Saved Reports, Brand Assignments, User preferences, the delete audit log and Golden Trio embeddings) lives in one Postgres database, using JSONB for flexible documents and pgvector for embeddings. The analytics data the agent queries stays in BigQuery; this decision covers only the state the agent itself keeps. The prototype uses SQLite behind the same repository interface.

We chose Postgres because the parts of the system that must be *correct* are relational and transactional, and because it is the one engine that also covers vectors and checkpoints without a second store:

- **Atomic delete and audit (Req 3).** Deleting Saved Reports and writing the audit record must be one transaction.
- **Relational access mapping (Req 2).** Brand Assignments are a User↔Brand mapping that access control depends on.
- **Vectors in the same database (Req 1).** pgvector's HNSW index is updated in the same transaction as the Trio row, so no separate vector store runs for the Golden Bucket.
- **An official LangGraph checkpointer.** `langgraph-checkpoint-postgres` is maintained with LangGraph ([ADR 0001](0001-langgraph-agent-framework.md)). The prototype uses its SQLite sibling, so moving to production is a change of saver, not new code.

JSONB is a supporting reason, not a deciding one. Today every JSONB column is read and written whole, which MySQL's `JSON` does as well. JSONB can be indexed as a whole with GIN, so querying inside reports or preferences later needs an index, not a schema change.

SQLite stays a prototype choice. It is a file on one machine with one writer at a time, so with several Chat API instances a confirmation paused on one could not resume on another, and its cosine search is a linear scan in Python.

## Considered Options

- **MySQL**: as transactional and relational as Postgres. But Community Edition can store vectors and not search them: `DISTANCE()` and vector indexes are HeatWave only ([MySQL 9.5 vector functions](https://docs.oracle.com/cd/E17952_01/mysql-9.5-en/vector-functions.html)). LangGraph has no official MySQL checkpointer. We would run a separate vector store and maintain our own saver. Rejected.
- **MongoDB**: a natural fit for document-shaped reports and evolving preference schemas, and Vector Search now runs on Community Edition as well as Atlas ([GA, June 2026](https://www.mongodb.com/products/updates/now-ga-mongodb-search-and-vector-search-for-community-edition/)). But it runs in a separate `mongot` process that syncs asynchronously, multi-document transactions are opt-in and heavier, and relational access mappings are awkward. Rejected.
- **Separate stores** (e.g. Firestore + a vector DB): each piece is best-of-breed, but there are more moving parts, no cross-store transactions, and the design is harder to explain and operate. Rejected.
