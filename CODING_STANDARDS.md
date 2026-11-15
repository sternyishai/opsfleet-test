# Coding standards

The rules `/code-review` checks every diff against. They are judgement calls a reviewer has to make by reading. Rules a machine can check live in tooling instead and are not repeated here: `ruff` (including the `banned-api` rule that keeps provider SDKs in `agent.adapters`), `pyright`, and the tests in `tests/test_skeleton.py`. The pre-commit hook in `.githooks/` runs all three.

Each rule names its source. If a rule and a design doc disagree, the doc wins and this file is fixed.

## 1. Where code goes

The package map and dependency rule are in [docs/lld/01-modules.md](docs/lld/01-modules.md).

- `domain/` holds records, `contracts/` holds input and output shapes, `ports/` holds `Protocol`s. None of them contain behaviour that calls anything outside the package.
- `adapters/` is the only package that talks to Gemini, BigQuery, SQLite, Phoenix or the filesystem outside `seed/` and `traces/`.
- `tools/`, `core/`, `graph/` and `evals/` depend on ports, never on an adapter module.
- `composition.py` is the only module that names a concrete adapter ([ADR 0006](docs/adr/0006-ports-and-adapters.md)).
- `cli.py` and `adapters/web/` only translate: text to `ChatService.handle`, commands to `Views`. A branch on business state in a channel is a violation ([LLD 07](docs/lld/07-web.md)).
- A new port needs two real adapters, a fake counting as one. Otherwise it is a module, not a seam ([LLD 01](docs/lld/01-modules.md#where-the-seams-are-and-why)).

## 2. Safety invariants

From [HLD 03](docs/hld/03-safety-and-pii.md) and [ADR 0003](docs/adr/0003-layered-data-access-enforcement.md).

- User, Author, role and Brand scope come from `TurnContext.user`, never from a tool input or model output.
- Every message to a User leaves through `Outbound.emit`. Nothing writes to stdout, a socket or a queue around it. The one other way out is a `Views` result, which runs the PII filter on every text field before a channel renders it ([LLD 07](docs/lld/07-web.md)).
- No SQL reaches `QueryRunner` without `QueryGuard.check` first, probes included. Brands are bound as parameters, never formatted into SQL.
- Result rows never enter `AgentState`, a trace or a log; only `ResultSummary` does ([LLD 03](docs/lld/03-graph-state.md#what-never-enters-state)).
- Only the router opens a branch. A tool result never decides routing.
- A safety step that cannot run fails closed (refuse), never open.
- A trace is a way out too. A change to what a span records (a new attribute, an error kept on a span, text from the User or the warehouse) is reviewed by attacking it like the guard below: a warehouse error or a filter can quote a Customer's email. `tests/conftest.py` fails any test that puts PII on a span; the review looks for the paths no test drives. In slice 5 this found a dry-run error carrying an email into the JSONL.
- A change to the query guard, the PII filter or the input rules is reviewed by attacking it: the Spec review runs hostile inputs against the real adapter (other Brands' literals in every comparison form, PII under other names, deep nesting), not only the diff. In slice 2 this found two holes the tests had missed.

## 3. Errors and outcomes

From [LLD 04 · Error model](docs/lld/04-contracts.md#error-model).

- Expected outcomes (rejected SQL, an empty result, nothing matched) are values in a discriminated union on `status`, not exceptions.
- Only the exceptions in `errors.py` cross a port. An adapter catches its provider's exceptions and classifies them.
- User-facing failure text comes from the message catalog (`MessageClass`), never from an exception message. A view's plain empty state ("You have no saved reports yet.") is output, not a failure, and may live in the channel's renderer.
- Text a User reads is written for non-technical executives: plain words, a next step, no warehouse or engineering terms (`tests/test_wording.py` checks the catalog). An example in a prompt or message comes from what is on the User's screen (an item number that exists in the plan shown), never a fixed sample: in slice 4 a one-report plan suggested "yes except 2".

## 4. Names

- Use the terms in [GLOSSARY.md](GLOSSARY.md): Brand Assignment, Saved Report, Author, Deletion Plan, Trio, Persona.
- **User** is an employee who uses the agent; **Customer** is a shopper in `thelook`. Never mix the two, including in variable names.
- Names say what a thing holds or does. If no honest name comes, the design is unclear: say so in the review.

## 5. Model calls

- Every separate model call has its own `ModelStep` and its own `MODEL_<STEP>` / `MODEL_<STEP>_FALLBACK` pair ([LLD 06 · LLMFactory](docs/lld/06-ports.md#llmfactory)).
- Prompts are data passed to the model, not built by string-joining untrusted text into instructions. Tool results and stored notes go in delimited blocks.

## 6. Tests

From the `tdd` skill and [LLD 05](docs/lld/05-tools.md).

- Tests sit at the seams named in the issue's "Tests (write first)" section and cross the same interface the graph uses.
- Ports are replaced by fakes. No test calls the real Gemini, BigQuery or the network.
- Safety tests never use a permissive fake `QueryGuard`.
- Expected values come from the spec or a worked example, never recomputed the way the code computes them.

## 7. Docs stay in step

- A change to a port, tool, schema, table, node or trace attribute updates the matching page in `docs/lld/` in the same PR ([docs/lld/README.md](docs/lld/README.md)).
- A decision that is hard to reverse gets an ADR in `docs/adr/`.

## 8. Readability

- One job per module and per function. A function that needs "and" to describe it is two functions.
- No code for a need the issue does not have: no unused parameters, hooks or abstract base classes.
- Comments and docstrings say *why*. A port's docstring is its full interface: invariants and error modes.
