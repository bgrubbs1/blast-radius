# Blast Radius

**Know what a schema change breaks — before you ship it.**

You are about to drop a column. Somewhere downstream there is a dashboard that the
finance team opens every Monday, a dbt model three hops away, and an ML feature
table nobody owns. DataHub already knows about all of them. Blast Radius asks it,
proves which ones actually break, tells you who to notify, and writes the
migration patches.

```console
$ blast-radius plan --change "ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount" --fail-on breaking

change: DROP COLUMN analytics.public.fct_orders.discount_amount
──────────────── blast radius — DROP COLUMN analytics.public.fct_orders.discount_amount ────────────────
verdict   asset                              type          hops owner             why
BREAKING  analytics.marts.dim_customer_ltv   dataset          1 maya.iyer         a query on this asset references … — `o.discount_amount`
BREAKING  analytics.marts.mart_orders_flat   dataset          1 sam.okafor        a query on this asset references … — `discount_amount`
AT RISK   analytics.marts.rpt_daily_revenue  dataset          1 finance-analytics exposes a column named 'discount_amount' of its own
AT RISK   Finance Exec Overview              dashboard        2 finance-analytics dashboard consuming the change — a human must confirm
AT RISK   order_propensity_v3                mlFeatureTable   2 (unowned)         mlFeatureTable consuming the change — a human must confirm
safe      analytics.staging.stg_orders_audit dataset          1 maya.iyer         1 indexed query parsed; none reference discount_amount

2 breaking · 3 at risk · 1 safe · 3 patches
$ echo $?
2
```

Exit code 2 means CI just stopped you from breaking the finance dashboard.

## Try it in 30 seconds, without DataHub

The demo replays recorded MCP responses from `fixtures/`, so it needs no
infrastructure at all:

```bash
pip install -e .
blast-radius demo --out out/
```

That writes `out/impact-report.md` (the artefact you paste into a PR),
`out/impact.json` (machine-readable), `out/notify.md`, and `out/patches/*.diff`.
Pre-generated copies live in [`examples/`](examples/).

## What it does that a lineage graph does not

Opening the lineage tab tells you *what is downstream*. It does not tell you what
**breaks**, and that difference is the entire job:

| | |
|---|---|
| **Proves impact from real queries** | Reads the SQL DataHub has indexed for each downstream asset and parses it (sqlglot). A verdict of `BREAKING` means a query names the column — quoted in the report. |
| **Separates "breaks" from "drifts"** | `SELECT *` over the changed table does not fail, but the asset's output schema silently changes. That is `AT_RISK`, reported differently from a hard break. |
| **Never calls unknown "safe"** | No indexed queries, or SQL we could not parse, is `AT_RISK` with the reason stated. Silence is not evidence. |
| **Writes the migration** | Renames are rewritten across every reference (AST, not regex) and keep the old name as an output alias so *their* consumers survive. Drops remove dead projections. |
| **Refuses to guess** | A column used in a `WHERE`, `JOIN` or `GROUP BY` encodes intent. Those patches come back marked `review` with a `TODO` at the top, never silently "fixed". |
| **Tells you who to talk to** | Owners and domains, grouped, worst-first — including an explicit `(unowned)` bucket, because an unowned breaking asset is its own finding. |
| **Feeds the catalog back** | `--write-back` marks the column deprecated in DataHub and tags impacted assets, so the next person to open that dataset sees the warning. |

## How DataHub is used

Everything comes from the [DataHub MCP Server](https://docs.datahub.com/docs/features/feature-guides/mcp)
(`uvx mcp-server-datahub@latest`), against **DataHub Core or DataHub Cloud**:

| MCP tool | What Blast Radius does with it |
|---|---|
| `search` | resolve a table name from DDL to a dataset URN |
| `get_lineage` | walk downstream N hops (datasets, dashboards, charts, ML feature tables) |
| `get_entities` | ownership, domain and display names for everything found |
| `list_schema_fields` | detect a same-named column propagated downstream |
| `get_dataset_queries` | the real SQL that proves or clears each asset |
| `get_me` | connectivity check in `blast-radius doctor` (DataHub Cloud only) |
| deprecation / tag / document tools | optional `--write-back` (needs `TOOLS_IS_MUTATION_ENABLED=true`) |

Tool **argument names are discovered from each tool's input schema** at connect
time rather than hardcoded, so a server release that renames `max_hops` to `hops`
degrades to a warning instead of a crash. `mcp` 2.0 exposes that schema as
`input_schema`; reading only the wire's camelCase `inputSchema` returns nothing and
leaves the client guessing argument names, which is exactly the bug fixed in
`357962b`. Both spellings are now tried, newest first.

## Install

```bash
git clone https://github.com/bgrubbs1/blast-radius
cd blast-radius
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requires Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) on PATH (for `uvx`).

### Point it at DataHub

```bash
# DataHub Core, local quickstart:
pip install acryl-datahub && datahub docker quickstart

export DATAHUB_GMS_URL=http://localhost:8080
export DATAHUB_GMS_TOKEN=<Settings → Access Tokens → Generate>

blast-radius doctor          # lists the tools the server actually exposes
```

`doctor` output tells you exactly what is wrong when something is: no DataHub, no
token, or no `uvx`.

## Usage

```bash
# a DDL string, a file, or piped stdin
blast-radius plan --change "ALTER TABLE db.orders RENAME COLUMN amt TO amount_usd"
blast-radius plan --change migration.sql --dialect snowflake --depth 3 --out out/

# a dbt model diff -- removed SELECT columns are treated as drops
git diff models/marts/fct_orders.sql | blast-radius plan --out out/

# gate a PR in CI
blast-radius plan --change migration.sql --fail-on breaking

# annotate DataHub with the outcome
blast-radius plan --change migration.sql --write-back

# add a prose summary from any OpenAI-compatible endpoint (LM Studio, vLLM, …)
blast-radius plan --change migration.sql --llm --llm-base-url http://localhost:1234/v1
```

Useful flags: `--urn` (skip search), `--depth` (lineage hops, default 2),
`--query-limit`, `--record` (save fixtures from a live run), `--offline` (replay
them), `--fail-on breaking|at-risk|never`.

### In CI

```yaml
# .github/workflows/schema-guard.yml
- run: pip install blast-radius
- run: blast-radius plan --change migrations/$(git diff --name-only | tail -1) --fail-on breaking --out out/
  env:
    DATAHUB_GMS_URL: ${{ secrets.DATAHUB_GMS_URL }}
    DATAHUB_GMS_TOKEN: ${{ secrets.DATAHUB_GMS_TOKEN }}
- uses: actions/upload-artifact@v4
  with: { name: blast-radius, path: out/ }
```

A working copy is in [`.github/workflows/schema-guard.yml`](.github/workflows/schema-guard.yml).

## Where the LLM is — and is not

The impact engine is **deterministic**. Verdicts come from parsed SQL and lineage,
not from a model, so the same change always produces the same report and a
reviewer can check every claim. An LLM is used for exactly one thing, only with
`--llm`: the prose summary at the top. The report labels that paragraph as model
output, and the model is explicitly forbidden from re-ranking severity.

This also means the tool works with **no API key and no cloud** — and if you do
want the summary, a local LM Studio endpoint is the default.

## Design notes

- **Evidence or nothing.** Every non-safe verdict carries an `Evidence` record
  with a quotable detail and, where there is one, the SQL snippet. If we cannot
  point at something, we say "unproven".
- **Expand/contract rollout.** The report's rollout order is the safe sequence for
  the specific operation (expand → migrate → verify → contract), not generic
  advice.
- **Fixtures are recorded from a real DataHub.** `fixtures/` contains the actual
  MCP responses from a DataHub Core 1.7.0 instance seeded by
  `scripts/seed_datahub.py`. `blast-radius demo` replays them and produces
  byte-identical findings to the live run, and `pytest` asserts on them: 50 tests,
  no network.
- **The adaptive layer is not theoretical.** Against the real server, `search`
  rejected the `filters` argument; the client parsed the rejected parameter out of
  the error, retried without it, and the run completed with a warning in the
  report. That is the mechanism working, recorded in `examples/impact-report.md`.

```bash
pytest -q                          # 50 passed
python scripts/check_examples.py   # fixtures still reproduce examples/

# re-record against your own DataHub
python scripts/seed_datahub.py --gms http://localhost:8080
blast-radius plan --change examples/migration.sql --depth 2 --record
```

## Layout

```
blastradius/
  change.py     DDL + dbt-diff parsing            (regex, validated by sqlglot)
  datahub.py    MCP client: discovery, replay, record
  extract.py    tolerant payload readers + SQL column-reference analysis
  impact.py     deterministic verdicts from lineage, schemas and queries
  patch.py      AST rewrites; mechanical vs review
  report.py     markdown / JSON / terminal rendering
  llm.py        optional prose summary (any OpenAI-compatible endpoint)
  writeback.py  optional DataHub annotations
  cli.py        plan / demo / doctor
scripts/        seed a local DataHub with the demo warehouse; example-drift guard
fixtures/       MCP responses recorded from a real DataHub Core instance
examples/       generated report, patches and notify list
```

## Limitations

- Column-level proof is only as good as DataHub's query index. Assets with no
  indexed queries are reported `AT_RISK`, never `SAFE`.
- Dashboards, charts and ML feature tables are judged by lineage alone; their
  definitions are not SQL that DataHub exposes.
- `parse_change` covers `DROP COLUMN`, `RENAME COLUMN`, a type change and
  `DROP TABLE`. Adding a column is not a breaking change and is out of scope.
- Patches are generated per indexed query, not per repository file — they are the
  starting point of a PR, not the whole PR.

## License

Apache 2.0 — see [LICENSE](LICENSE). Built for
[Build with DataHub: The Agent Hackathon](https://datahub.devpost.com/).
Pre-existing code disclosure: [DISCLOSURES.md](DISCLOSURES.md).
