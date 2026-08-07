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

**It was built against a real DataHub and adapted to what the server actually
does.** The MCP server advertises an empty `inputSchema`, so argument names
cannot be introspected: the client sends documented names, and when the server
rejects one it parses the rejected parameter out of the error and retries without
it. That is not hypothetical — `search` rejects `filters`, and the recorded run
in `examples/impact-report.md` shows the retry and the completed analysis.
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

Findings worth filing upstream from building this, if time allows before the
deadline:

1. `mcp-server-datahub` advertises an empty `inputSchema` for every tool, so MCP
   clients cannot introspect arguments and must guess. Reporting this with the
   observed tool list is a concrete, reproducible issue.
2. Pydantic argument-validation failures are returned as ordinary text content
   with `isError` unset, so well-behaved clients treat a rejection as data.

## Pre-submission checklist

- [x] Public repo, Apache 2.0 licensed
- [x] Full setup + run instructions in the README
- [x] Uses DataHub via the MCP Server (6 tools + mutation write-back)
- [x] Newly created during the submission period (see `DISCLOSURES.md`)
- [x] Sample outputs in `examples/`
- [x] Verified end-to-end against DataHub Core 1.7.0, not only fixtures
- [ ] **Demo video uploaded to YouTube as public** and the link pasted in the form
- [ ] Form submitted before Aug 10, 5:00 PM EDT
