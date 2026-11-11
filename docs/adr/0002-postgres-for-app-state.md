# Postgres for all application state

All application state (conversation checkpoints, Saved Reports, Brand Assignments, User preferences, the delete audit log and Golden Trio embeddings) lives in one Postgres database, using JSONB for flexible documents and pgvector for embeddings. The prototype uses SQLite behind the same repository interface. We chose Postgres over MongoDB because the parts of the system that must be *correct* are relational and transactional. Deleting Saved Reports and writing the audit record must be one atomic transaction (Req 3). Brand Assignments are a relational User↔Brand mapping that access control depends on (Req 2). Keeping vectors in the same database avoids running a separate vector store for the Golden Bucket (Req 1). Postgres is also portable: Cloud SQL, AlloyDB, RDS or any self-hosted instance.

## Considered Options

- **MongoDB**: a natural fit for document-shaped reports and evolving preference schemas, but multi-document transactions are opt-in and heavier, relational access mappings are awkward, and vector search is tied to Atlas.
- **Separate stores** (e.g. Firestore + a vector DB): each piece is best-of-breed, but there are more moving parts, no cross-store transactions, and the design is harder to explain and operate.
