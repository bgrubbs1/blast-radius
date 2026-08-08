# Devpost submission packet — Build with DataHub: The Agent Hackathon

Everything the submission form asks for, ready to paste. Deadline **August 10,
2026, 5:00 PM EDT**. Submit at <https://datahub.devpost.com/>.

---

## Project name

**Blast Radius**

## Elevator pitch (200 char limit)

> Proves which downstream assets a schema change actually breaks — from the
> queries DataHub already indexed — then writes the migration patches and names
> the owners to notify.

(197 characters.)

## Challenge category

**Agents That Do Real Work** — with a strong secondary fit for *Metadata-Aware
Code Generation & Development*, since the agent emits migration patches derived
from catalog metadata. Submit under one; mention the other in the description.

## Repository

<https://github.com/bgrubbs1/blast-radius> — public, Apache 2.0
(`LICENSE`), full setup instructions in `README.md`.

## Demo video

`docs/demo.mp4` in the repo → upload to YouTube as **public** (not "unlisted
only" — the rules require a public link) and paste the URL. Under 3 minutes.

## Text description

### What it does

You are about to drop a column. Somewhere downstream there is a dashboard the
finance team opens every Monday, a dbt model three hops away, and an ML feature
table nobody owns. DataHub already knows about all of them.

Blast Radius takes a proposed change — DDL or a dbt model diff — and answers the
question a lineage graph does not: **what breaks?**

1. Resolves the table to a dataset URN (`search`).
2. Walks downstream lineage (`get_lineage`), collecting datasets, dashboards,
   charts and ML feature tables.
3. For each one, pulls the SQL DataHub has indexed (`get_dataset_queries`) and
   parses it with sqlglot to find real references to the changed column, plus
   the schema (`list_schema_fields`) to spot a propagated column of the same name.
4. Classifies every asset with evidence you can check:
   - **BREAKING** — a query names the column; the report quotes it.
   - **AT RISK** — `SELECT *` schema drift, a propagated column, SQL that would
     not parse, or no indexed queries at all. Unproven is never called safe.
   - **SAFE** — every indexed query parsed and none referenced the column.
5. Writes the migration: renames are rewritten across every reference at the AST
   level and keep the old name as an output alias so *their* consumers survive;
   dead projections are removed. A column used in a `WHERE`, `JOIN` or `GROUP BY`
   comes back marked `review` with a `TODO` — it encodes intent the tool will not
   invent.
6. Names who to tell (`get_entities` → owners, domains), grouped worst-first,
   with an explicit `(unowned)` bucket because an unowned breaking asset is its
   own finding.
7. Optionally writes back to DataHub (`--write-back`): a deprecation note on the
   column and a tag on impacted assets, so the next person to open that dataset
   sees the warning.

`--fail-on breaking` exits non-zero, so a pull request can be gated on it. A
ready GitHub Action is included that comments the report on the PR.

### How DataHub is used

Entirely through the **DataHub MCP Server** (`uvx mcp-server-datahub@latest`),
against DataHub Core or DataHub Cloud: `search`, `get_lineage`, `get_entities`,
`list_schema_fields`, `get_dataset_queries`, and the deprecation/tag/document
mutation tools for write-back. The catalog is not a nice-to-have here — the
column-level evidence *is* DataHub's query index.

### What makes it different

**The verdicts are deterministic.** No model decides whether something breaks;
SQL parsing and lineage do. The same change always produces the same report and
every claim cites its evidence. An LLM is used for exactly one thing, only with
`--llm`: the prose summary, which the report labels as model output and which is
forbidden from re-ranking severity. The tool needs no API key and no cloud —
point `--llm-base-url` at LM Studio if you want the paragraph.

**It was built against a real DataHub, and the hard part was checking our own
work.** Mid-build the client began silently dropping arguments: entity-type
filtering on `search` was applying to nothing, and `--query-limit` was ignored on
every run. The obvious conclusion was that the MCP server published incomplete
tool schemas, and this project was one commit away from filing that upstream.

That claim was checked at the wire level with raw JSON-RPC first. **The server
was innocent.** It sends complete schemas for every tool — this client read
`inputSchema`, while `mcp` 2.0 exposes it as `input_schema`. Seeing nothing, the
retry logic had started guessing parameter names, sent `filters` where the tool
takes `filter`, had the argument rejected, dropped it, and still got results back
— so nothing ever looked broken.

Fixed in `357962b`, re-verified against DataHub Core with the fixtures
re-recorded, and `examples/` regenerated so the recorded tool calls show the
correct arguments. The lesson is the one the tool itself is built on: an unproven
claim is not a safe claim, whether it is about a downstream dashboard or about
someone else's server.

Lineage payloads also carry owners, domains and platforms; those are filtered out
so corpusers are never reported as impacted assets.

### Technologies

Python 3.10+, the MCP Python SDK (stdio transport), `mcp-server-datahub`,
DataHub Core 1.7.0, sqlglot for SQL parsing and AST rewrites, rich for terminal
output, httpx for the optional LLM call. Apache 2.0.

### Data used

A synthetic Snowflake-shaped warehouse created for the demo and emitted into
DataHub by `scripts/seed_datahub.py` (5 datasets, lineage, ownership, domains,
and Query entities carrying the SQL). No proprietary or customer data. Owner
names are fictional.

## Sample outputs (recommended by the rules)

`examples/` in the repo:

| File | What it is |
|---|---|
| `impact-report.md` | the full generated report — verdicts, evidence, owners, rollout order |
| `impact.json` | the same run, machine-readable, for CI |
| `notify.md` | who to tell, grouped by owner |
| `patches/*.diff` + `*.sql` | generated migration patches (1 mechanical, 2 flagged for review) |
| `migration.sql`, `dbt-model.diff` | the two supported input shapes |

## Try it in 30 seconds (for judges)

```bash
git clone https://github.com/bgrubbs1/blast-radius && cd blast-radius
pip install -e ".[dev]"
blast-radius demo --out out/     # replays MCP responses recorded from a real DataHub
pytest -q                        # 50 passed
```

Against your own DataHub:

```bash
export DATAHUB_GMS_URL=http://localhost:8080
export DATAHUB_GMS_TOKEN=<token>
blast-radius doctor
blast-radius plan --change "ALTER TABLE db.orders DROP COLUMN discount_amount" --out out/
```

## Open-source contribution (bonus criterion)

**No upstream issue was filed, and the reason is the honest one.** Two findings
against `mcp-server-datahub` were drafted and then withdrawn:

1. *"The server advertises an empty `inputSchema` for every tool."* — **False.**
   Verified at the wire level with raw JSON-RPC: the server sends complete
   schemas. The defect was in this client reading `inputSchema` instead of
   `input_schema`. Fixed here in `357962b`; nothing to report upstream.
2. *"Pydantic argument-validation failures come back as ordinary text content
   with `isError` unset."* — **Unverified.** This was observed while the client
   was sending wrong argument names, so the premise is contaminated. It has not
   been re-tested against correct arguments and must not be filed as-is.

Claiming this bonus would mean publishing a bug report that did not survive
verification. It is not claimed.

## Pre-submission checklist

- [x] Public repo, Apache 2.0 licensed
- [x] Full setup + run instructions in the README
- [x] Uses DataHub via the MCP Server (6 tools + mutation write-back)
- [x] Newly created during the submission period (see `DISCLOSURES.md`)
- [x] Sample outputs in `examples/`
- [x] Verified end-to-end against DataHub Core 1.7.0, not only fixtures
- [x] `examples/` regenerated after `357962b` so recorded tool calls show the
      corrected arguments (`filter`, `limit`, `count`)
- [x] GitHub repository verified public; GitHub detects the Apache-2.0 license
- [ ] **Push the local submission-ready commits to `origin/main`** so judges see
      the corrected examples, recaptured demo, gallery, and README
- [ ] **Demo video uploaded to YouTube as public** and the link pasted in the form
- [ ] Form submitted before Aug 10, 5:00 PM EDT
