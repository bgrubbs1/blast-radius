from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_schema_guard_analyzes_every_changed_sql_file():
    workflow = (ROOT / ".github" / "workflows" / "schema-guard.yml").read_text(
        encoding="utf-8"
    )

    assert "head -1" not in workflow
    assert "changed-files.txt" in workflow
    assert "while IFS= read -r file" in workflow


def test_schema_guard_declares_comment_permission():
    workflow = (ROOT / ".github" / "workflows" / "schema-guard.yml").read_text(
        encoding="utf-8"
    )

    assert "issues: write" in workflow
