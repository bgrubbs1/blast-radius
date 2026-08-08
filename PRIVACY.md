# Privacy and work-data boundary

The public Blast Radius contest entry contains only the synthetic warehouse
created by `scripts/seed_datahub.py`. The bundled fixtures, examples, gallery
images, and demo video use fictional owners and do not contain employer,
customer, or proprietary data.

Live use is different. Blast Radius can read and report:

- dataset, dashboard, chart, and query names and URNs;
- schemas, columns, lineage, domains, descriptions, and owner identities;
- indexed SQL and generated SQL patches; and
- the proposed schema change and MCP tool-call arguments.

Treat those fields as confidential catalog data. Do not point this public
checkout or its GitHub Actions workflows at an employer or customer DataHub.
Use a private, access-controlled repository and an approved least-privilege
token for authorized work-catalog analysis.

## Persistence and publication

`--out` writes detailed Markdown, JSON, notification, and patch files. Keep
those files in a private location when the analysis uses non-public data.

`--record` writes decoded MCP responses, which can contain full SQL, schemas,
URNs, descriptions, domains, and owner identities. Recording therefore
requires an explicit `--fixtures` directory outside the bundled synthetic
fixtures. `.private-fixtures/` is ignored for local, authorized captures. Never
commit or upload a live work-catalog capture without an independent
sanitization and approval review.

## Model and process egress

The deterministic analysis does not need an LLM. `--llm` defaults to a local
loopback endpoint. A non-local endpoint requires `--allow-remote-llm` and sends
the change, root dataset, asset names and types, owner names, lineage hop
counts, evidence details, result counts, and patch metadata. It does not send
full SQL statements or full patches. Confirm the data owner and provider policy
permit that transfer before enabling it.

The external DataHub MCP process receives only a minimal launch environment,
the configured DataHub URL and token, and the mutation flag when explicitly
enabled. Unrelated parent-shell credentials are not inherited.

Supply the DataHub token through `DATAHUB_GMS_TOKEN`; Blast Radius does not
accept tokens as command-line arguments because command lines can be retained
in shell history and process diagnostics.

## Public CI

The checked-in `schema-guard.yml` intentionally uses only the bundled
synthetic fixtures and receives no DataHub secrets. A live schema guard belongs
in a private repository with protected credentials, trusted code, redacted
public summaries, and private detailed reports.
