from __future__ import annotations

import pytest

from blastradius.change import parse_change
from blastradius.cli import FIXTURES, _build_parser, _recording_dir_error, main
from blastradius.datahub import _mcp_child_env
from blastradius.llm import is_remote_endpoint
from blastradius.models import Evidence, ImpactReport, ImpactedAsset, Owner, Verdict
from blastradius.report import to_markdown


CHANGE = "ALTER TABLE analytics.public.fct_orders DROP COLUMN discount_amount"


def test_mcp_child_does_not_inherit_unrelated_secrets(monkeypatch):
    monkeypatch.setenv("WORK_SECRET", "must-not-cross-the-boundary")
    monkeypatch.setenv("PATH", "safe-path")

    env = _mcp_child_env("http://localhost:8080", "datahub-token")

    assert "WORK_SECRET" not in env
    assert env["PATH"] == "safe-path"
    assert env["DATAHUB_GMS_TOKEN"] == "datahub-token"
    assert env["DATAHUB_GMS_URL"] == "http://localhost:8080"


def test_live_recording_requires_a_separate_private_directory(tmp_path):
    assert _recording_dir_error(None)
    assert _recording_dir_error(FIXTURES)
    assert _recording_dir_error(tmp_path / "private") is None


def test_cli_blocks_default_recording_before_connecting(capsys):
    assert main(["plan", "--change", CHANGE, "--record"]) == 1
    assert "explicit --fixtures" in capsys.readouterr().out


def test_cli_does_not_accept_tokens_in_process_arguments():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["doctor", "--token", "secret"])


@pytest.mark.parametrize(
    ("url", "provider", "remote"),
    [
        (None, "openai", False),
        ("http://127.0.0.1:8000/v1", "openai", False),
        ("http://[::1]:8000/v1", "openai", False),
        ("https://models.example.com/v1", "openai", True),
        (None, "anthropic", True),
    ],
)
def test_remote_llm_detection(url, provider, remote):
    assert is_remote_endpoint(url, provider) is remote


def test_cli_blocks_remote_llm_without_explicit_egress_consent(capsys):
    code = main(
        [
            "plan",
            "--change",
            CHANGE,
            "--llm",
            "--llm-base-url",
            "https://models.example.com/v1",
        ]
    )
    assert code == 1
    output = capsys.readouterr().out
    assert "--allow-remote-llm" in output
    assert "owner names" in output


def test_markdown_normalizes_catalog_controlled_content():
    change = parse_change(CHANGE)
    asset = ImpactedAsset(
        urn="urn:li:dataset:(demo)",
        name="orders\n# injected @reviewers",
        entity_type="dataset|admin",
        verdict=Verdict.BREAKING,
        owners=[Owner("urn:li:corpuser:demo", "owner\n[click](bad)")],
        evidence=[Evidence("query", "proof\n> fake quote", snippet="```escape")],
    )
    report = ImpactReport(change=change, root_urn=asset.urn, assets=[asset])

    markdown = to_markdown(report)

    assert "\n# injected" not in markdown
    assert "@reviewers" not in markdown
    assert "&#64;reviewers" in markdown
    assert "owner [click](bad)" not in markdown
    assert "dataset|admin" not in markdown
