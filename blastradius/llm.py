"""Optional prose layer.

The LLM writes the executive summary and nothing else. It is handed the
findings *after* they are final, and its output is inserted under a "Summary"
heading that the report marks as model-written. If the endpoint is missing,
slow, or wrong, ``narrate`` returns ``""`` and the report is still complete --
that is the whole reason this file is 90 lines and not the core of the tool.

Any OpenAI-compatible endpoint works, which includes a local LM Studio or
llama.cpp server, so the tool never requires a paid API to run.
"""

from __future__ import annotations

import json
import os
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import ImpactReport, Verdict

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "local-model"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You write short, plain-spoken impact summaries for data engineers \
reviewing a schema change.

Rules you must follow:
- Use ONLY the findings given to you. Never invent an asset, owner, or number.
- Do not re-rank severity. If the findings say an asset is at risk, it is at risk,
  not broken; if they say breaking, do not soften it.
- Lead with what will break and who needs to act. No preamble, no restating the
  question, no closing summary.
- At most 150 words, prose (no headings, no bullet lists longer than three items).
"""


def resolved_endpoint(base_url: str | None = None, provider: str = "openai") -> str:
    """Return the exact endpoint that would receive the findings payload."""

    if provider == "anthropic":
        return ANTHROPIC_URL
    root = (
        base_url or os.environ.get("BLAST_RADIUS_LLM_BASE_URL") or DEFAULT_BASE_URL
    ).rstrip("/")
    return f"{root}/chat/completions"


def is_remote_endpoint(base_url: str | None = None, provider: str = "openai") -> bool:
    """True when the findings payload would leave the local machine."""

    hostname = urlparse(resolved_endpoint(base_url, provider)).hostname
    if not hostname:
        return True
    if hostname.casefold() == "localhost":
        return False
    try:
        return not ip_address(hostname).is_loopback
    except ValueError:
        return True


def _findings_payload(report: ImpactReport) -> dict[str, Any]:
    """A compact, quotable view of the report -- never the whole object."""

    def describe(verdict: Verdict) -> list[dict[str, Any]]:
        out = []
        for asset in report.by_verdict(verdict)[:12]:
            top = asset.worst_evidence()
            out.append(
                {
                    "asset": asset.name,
                    "type": asset.entity_type,
                    "hops": asset.hops,
                    "owners": [o.name for o in asset.owners] or ["(unowned)"],
                    "evidence": top.detail if top else None,
                }
            )
        return out

    return {
        "change": report.change.describe(),
        "dataset": report.root_urn or report.change.table,
        "breaking": describe(Verdict.BREAKING),
        "at_risk": describe(Verdict.AT_RISK),
        "safe_count": len(report.safe),
        "patches": [
            {"title": p.title, "confidence": p.confidence, "note": p.note}
            for p in report.patches[:8]
        ],
    }


def narrate(
    report: ImpactReport,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
    provider: str = "openai",
) -> tuple[str, str | None]:
    """Return ``(summary, warning)``. Both may be empty/None."""
    payload = json.dumps(_findings_payload(report), indent=2)
    base_url = (base_url or os.environ.get("BLAST_RADIUS_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = model or os.environ.get("BLAST_RADIUS_LLM_MODEL") or DEFAULT_MODEL
    api_key = api_key or os.environ.get("BLAST_RADIUS_LLM_API_KEY") or ""

    try:
        if provider == "anthropic":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return "", "no ANTHROPIC_API_KEY set -- skipped the prose summary"
            response = httpx.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model if model != DEFAULT_MODEL else "claude-sonnet-5",
                    "max_tokens": 400,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": payload}],
                },
                timeout=timeout,
            )
            response.raise_for_status()
            blocks = response.json().get("content", [])
            return "".join(b.get("text", "") for b in blocks).strip(), None

        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "max_tokens": 400,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        if not choices:
            return "", "the LLM endpoint returned no choices"
        return (choices[0].get("message", {}).get("content") or "").strip(), None
    except Exception as exc:  # noqa: BLE001 - prose is strictly optional
        return "", f"prose summary skipped: {type(exc).__name__}: {exc}"
