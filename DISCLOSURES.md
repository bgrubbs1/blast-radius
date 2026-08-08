# Disclosures

Per the hackathon rules, everything a judge might want to know about the
provenance of this code.

## Newly created during the submission period

All source in this repository was written for **Build with DataHub: The Agent
Hackathon** during the submission period (July 6 – August 10, 2026). There is no
pre-existing project underneath it and no code carried over from earlier work.

## Third-party components

Standard, unmodified, installed from PyPI (see `pyproject.toml`):

| Package | License | Used for |
|---|---|---|
| `mcp` | MIT | MCP client (stdio transport to the DataHub MCP Server) |
| `sqlglot` | MIT | SQL parsing, column-reference analysis, AST rewrites |
| `rich` | MIT | terminal rendering |
| `httpx` | BSD-3-Clause | HTTP call to the optional LLM endpoint |
| `pytest` (dev) | MIT | tests |
| `Pillow` (video extra) | HPND | renders the optional demo video |

The DataHub MCP Server itself (`mcp-server-datahub`) is run as an external
process via `uvx` and is not vendored or modified.

## AI assistance

This project was written with AI coding assistance (Claude and Codex). The design,
architecture and verification approach are the author's; all generated code was
reviewed, and correctness is demonstrated by the 57-test suite and by the
end-to-end fixtures in `fixtures/`, which replay DataHub-shaped MCP payloads
through the full pipeline.

## Data

No proprietary or customer data is included. `fixtures/` and `examples/` describe
a synthetic Snowflake-style warehouse (`analytics.public.fct_orders` and friends)
created for the demo; the owner names in it are fictional.

The public pull-request workflow uses only those synthetic fixtures and receives
no DataHub credentials. Live recordings require an explicit private directory,
remote LLM use requires an explicit egress acknowledgement, and the external MCP
child receives a minimal environment rather than unrelated shell credentials.
See `PRIVACY.md` for the complete data-flow boundary.
