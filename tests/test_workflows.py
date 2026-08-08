from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_schema_guard_analyzes_every_changed_sql_file():
    workflow = (ROOT / ".github" / "workflows" / "schema-guard.yml").read_text(
        encoding="utf-8"
    )

    assert "head -1" not in workflow
    assert "changed-files.txt" in workflow
    assert "while IFS= read -r file" in workflow


def test_public_schema_guard_has_no_work_credentials_or_comment_sink():
    workflow = (ROOT / ".github" / "workflows" / "schema-guard.yml").read_text(
        encoding="utf-8"
    )

    assert "DATAHUB_GMS_TOKEN" not in workflow
    assert "DATAHUB_GMS_URL" not in workflow
    assert "issues: write" not in workflow
    assert "createComment" not in workflow
    assert "--offline" in workflow
    assert "--fixtures fixtures" in workflow
