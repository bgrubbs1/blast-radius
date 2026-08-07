"""Thin, defensive client for the DataHub MCP Server.

Everything Blast Radius knows about your data platform arrives through this
file, and it arrives over MCP -- ``uvx mcp-server-datahub@latest`` talking to
either DataHub Core or DataHub Cloud.

Two design decisions worth knowing about:

**Argument names adapt to the server.** The DataHub MCP server validates
arguments at runtime and advertises an *empty* ``inputSchema``, so there is
nothing to introspect: we send the documented parameter names and, if the server
rejects one, parse the rejected keys out of its error, drop them and retry. A
release that renames or removes a parameter therefore degrades to a warning in
the report instead of a stack trace. When a server *does* publish a schema, that
is used first.

**Every response is cached and can be replayed.** ``--record`` writes each MCP
response to ``fixtures/``; ``--offline`` replays them. That is what makes
``blast-radius demo`` work on a laptop with no DataHub, and it is what makes
the tests deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

DEFAULT_GMS_URL = "http://localhost:8080"
MCP_PACKAGE = "mcp-server-datahub@latest"

# Logical call -> candidate parameter names, most likely first.
_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "query": ("query", "keywords", "q", "search_query"),
    "urn": ("urn", "entity_urn", "dataset_urn"),
    "urns": ("urns", "entity_urns", "urn"),
    "direction": ("direction", "lineage_direction"),
    "hops": ("max_hops", "hops", "depth", "degree", "num_hops"),
    "entity_types": ("entity_types", "types", "entity_type", "filters"),
    "limit": ("num_results", "limit", "count", "first"),
    "start": ("start", "offset"),
}


class DataHubError(RuntimeError):
    """Raised when the MCP server cannot be reached or a tool call fails hard."""


def _fixture_key(tool: str, args: dict[str, Any]) -> str:
    blob = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"{tool}.{digest}.json"


class DataHubMCP:
    """Async client. Use as ``async with DataHubMCP(...) as hub:``."""

    def __init__(
        self,
        gms_url: str | None = None,
        token: str | None = None,
        fixtures_dir: Path | None = None,
        offline: bool = False,
        record: bool = False,
        mutations: bool = False,
    ) -> None:
        self.gms_url = gms_url or os.environ.get("DATAHUB_GMS_URL") or DEFAULT_GMS_URL
        self.token = token or os.environ.get("DATAHUB_GMS_TOKEN") or ""
        self.fixtures_dir = fixtures_dir
        self.offline = offline
        self.record = record
        self.mutations = mutations

        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._schemas: dict[str, dict[str, Any]] = {}
        self.tool_calls: list[str] = []
        self.warnings: list[str] = []

    # -- lifecycle -----------------------------------------------------------

    async def __aenter__(self) -> DataHubMCP:
        if self.offline:
            if not self.fixtures_dir or not self.fixtures_dir.exists():
                raise DataHubError(
                    f"offline mode needs recorded fixtures; {self.fixtures_dir} is missing"
                )
            manifest = self.fixtures_dir / "_tools.json"
            if manifest.exists():
                self._schemas = json.loads(manifest.read_text(encoding="utf-8"))
            return self

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise DataHubError(f"the 'mcp' package is required: {exc}") from exc

        env = dict(os.environ)
        env["DATAHUB_GMS_URL"] = self.gms_url
        if self.token:
            env["DATAHUB_GMS_TOKEN"] = self.token
        if self.mutations:
            env["TOOLS_IS_MUTATION_ENABLED"] = "true"

        params = StdioServerParameters(
            command=os.environ.get("BLAST_RADIUS_MCP_COMMAND", "uvx"),
            args=os.environ.get("BLAST_RADIUS_MCP_ARGS", MCP_PACKAGE).split(),
            env=env,
        )
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            listing = await self._session.list_tools()
            for tool in listing.tools:
                self._schemas[tool.name] = getattr(tool, "inputSchema", {}) or {}
            if self.record and self.fixtures_dir:
                self.fixtures_dir.mkdir(parents=True, exist_ok=True)
                (self.fixtures_dir / "_tools.json").write_text(
                    json.dumps(self._schemas, indent=2, sort_keys=True), encoding="utf-8"
                )
        except Exception as exc:
            await self.__aexit__(type(exc), exc, None)
            raise DataHubError(
                f"could not start the DataHub MCP server ({MCP_PACKAGE}) against "
                f"{self.gms_url}: {exc}"
            ) from exc
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # pragma: no cover - shutdown races are not fatal
                pass
            self._stack = None
            self._session = None

    # -- introspection -------------------------------------------------------

    @property
    def available_tools(self) -> list[str]:
        return sorted(self._schemas)

    def has_tool(self, *names: str) -> str | None:
        """Return the first of ``names`` the server exposes."""
        for name in names:
            if name in self._schemas:
                return name
        return None

    def _properties(self, tool: str) -> dict[str, Any]:
        schema = self._schemas.get(tool) or {}
        props = schema.get("properties")
        return props if isinstance(props, dict) else {}

    def _arg_name(self, tool: str, logical: str) -> str | None:
        """Map a logical argument onto this server's actual parameter name."""
        props = self._properties(tool)
        for candidate in _ARG_ALIASES.get(logical, (logical,)):
            if candidate in props:
                return candidate
        # No schema at all (offline replay, or a server that omits it): assume
        # the canonical spelling and let the call fail loudly if wrong.
        return _ARG_ALIASES.get(logical, (logical,))[0] if not props else None

    def _build_args(self, tool: str, **logical: Any) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for key, value in logical.items():
            if value is None:
                continue
            name = self._arg_name(tool, key)
            if name is None:
                self.warnings.append(
                    f"{tool}: server does not accept '{key}' -- omitted"
                )
                continue
            if key == "urns" and name in {"urn", "entity_urn", "dataset_urn"}:
                value = value[0] if isinstance(value, list) and value else value
            args[name] = value
        return args

    # -- raw call ------------------------------------------------------------

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Call an MCP tool and return its decoded payload.

        Returns ``None`` when the tool is unavailable or errors -- callers treat
        missing evidence as "unknown", never as "safe".
        """
        label = f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
        path = (
            self.fixtures_dir / _fixture_key(tool, args) if self.fixtures_dir else None
        )

        if self.offline:
            if path and path.exists():
                self.tool_calls.append(f"[replay] {label}")
                return json.loads(path.read_text(encoding="utf-8"))
            self.warnings.append(f"no fixture recorded for {label}")
            return None

        if self._session is None:
            raise DataHubError("not connected: use 'async with DataHubMCP(...)'")

        self.tool_calls.append(label)
        attempt = dict(args)
        result = None
        for _ in range(3):
            try:
                result = await self._session.call_tool(tool, attempt)
            except Exception as exc:
                self.warnings.append(f"{tool} failed: {exc}")
                return None

            # A rejected argument does not always come back as isError: this
            # server reports pydantic validation failures as ordinary text
            # content. So look for rejected parameter names either way.
            text = _as_text(result)
            rejected = _rejected_arguments(text)
            removable = [key for key in rejected if key in attempt]
            if not removable and not getattr(result, "isError", False):
                break

            # Drop exactly the parameters the server named and retry, so a
            # renamed or removed argument costs a warning rather than a finding.
            if not removable:
                self.warnings.append(f"{tool} returned an error: {text[:300]}")
                return None
            for key in removable:
                attempt.pop(key, None)
            self.warnings.append(
                f"{tool}: server rejected {', '.join(removable)} -- retried without"
            )
        else:
            self.warnings.append(f"{tool}: gave up after retries")
            return None

        payload = _decode(result)
        if self.record and path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        return payload

    # -- logical operations --------------------------------------------------

    @staticmethod
    def _structured_query(text: str) -> str:
        """Turn a table name into DataHub's structured search syntax.

        The server's own guidance: start with ``/q`` and join terms with ``+``
        rather than quoting, because ``+`` handles the underscores and dots in
        table names correctly.
        """
        terms = [t for t in re.split(r"[^A-Za-z0-9]+", text) if t]
        return "/q " + "+".join(terms) if terms else "/q *"

    async def search(
        self, query: str, entity_types: list[str] | None = None, limit: int = 10
    ) -> Any:
        tool = self.has_tool("search", "search_entities")
        if not tool:
            self.warnings.append("no search tool exposed by the MCP server")
            return None
        args: dict[str, Any] = {"query": self._structured_query(query)}
        if limit:
            args["num_results"] = limit
        if entity_types:
            # Entity filtering uses the server's SQL-like filter syntax.
            args["filters"] = " OR ".join(
                f"entity_type = {t.lower()}" for t in entity_types
            )
        return await self.call(tool, args)

    async def get_entities(self, urns: list[str]) -> Any:
        tool = self.has_tool("get_entities", "get_entity", "get_dataset")
        if not tool:
            return None
        return await self.call(tool, {"urns": list(urns)})

    async def get_lineage(
        self, urn: str, direction: str = "DOWNSTREAM", hops: int = 2
    ) -> Any:
        tool = self.has_tool("get_lineage", "lineage")
        if not tool:
            self.warnings.append("no lineage tool exposed by the MCP server")
            return None
        args: dict[str, Any] = {
            "urn": urn,
            "upstream": direction.upper() == "UPSTREAM",
            "max_hops": hops,
        }
        # `column: null` asks for whole-dataset lineage rather than one field.
        args["column"] = None
        return await self.call(tool, args)

    async def list_schema_fields(self, urn: str) -> Any:
        tool = self.has_tool("list_schema_fields", "get_schema", "schema_fields")
        if not tool:
            return None
        return await self.call(tool, {"urn": urn})

    async def get_dataset_queries(self, urn: str, limit: int = 25) -> Any:
        tool = self.has_tool("get_dataset_queries", "find_sql_context", "get_queries")
        if not tool:
            return None
        return await self.call(tool, {"urn": urn})

    async def whoami(self) -> Any:
        tool = self.has_tool("get_me", "whoami")
        return await self.call(tool, {}) if tool else None


# -- payload decoding --------------------------------------------------------


_REJECTED_LOC_RE = re.compile(r"'loc':\s*\(\s*'([^']+)'")
_REJECTED_JSON_RE = re.compile(r'"loc":\s*\[\s*"([^"]+)"')
_UNEXPECTED_RE = re.compile(r"unexpected keyword argument '([^']+)'", re.IGNORECASE)


def _rejected_arguments(error_text: str) -> list[str]:
    """Parameter names the server complained about, in the order reported.

    Covers the shapes seen in practice: pydantic's tuple ``loc``, its JSON
    ``loc`` array, and a plain Python ``TypeError`` message.
    """
    names: list[str] = []
    for pattern in (_REJECTED_LOC_RE, _REJECTED_JSON_RE, _UNEXPECTED_RE):
        for match in pattern.finditer(error_text or ""):
            name = match.group(1)
            if name not in names:
                names.append(name)

    # pydantic's plain-text form puts the field name on its own line above the
    # message:
    #     1 validation error for call[search]
    #     filters
    #       Unexpected keyword argument [type=unexpected_keyword_argument, ...]
    lines = (error_text or "").splitlines()
    for index, line in enumerate(lines):
        if "unexpected keyword argument" not in line.lower():
            continue
        for previous in reversed(lines[:index]):
            candidate = previous.strip().strip("'\"")
            if not candidate or candidate.lower().startswith(("1 validation", "validation error")):
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate) and candidate not in names:
                names.append(candidate)
            break
    return names


def _as_text(result: Any) -> str:
    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _decode(result: Any) -> Any:
    """Turn an MCP tool result into JSON where possible, else raw text.

    Prefers ``structuredContent`` when the server provides it; otherwise walks
    the content blocks and json-decodes any that parse.
    """
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    decoded: list[Any] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            decoded.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            decoded.append(text)
    if not decoded:
        return None
    return decoded[0] if len(decoded) == 1 else decoded
