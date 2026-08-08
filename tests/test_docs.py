from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_readme_matches_the_canonical_demo_and_public_media():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "2 breaking · 2 at risk · 1 safe · 3 patches" in readme
    assert "order_propensity_v3" not in readme
    assert "https://youtu.be/RT65Dc0qxLA" in readme
    assert "docs/gallery/1-verdicts.jpg" in readme
    assert "pip install blast-radius" not in readme


def test_submission_packet_records_the_live_submission_state():
    packet = (ROOT / "SUBMISSION.md").read_text(encoding="utf-8")

    assert "Metadata-Aware Code Generation & Development" in packet
    assert "https://devpost.com/software/blast-radius-ked6l8" in packet
    assert "- [ ]" not in packet


def test_writeback_claims_match_the_supported_mutation_tools():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    packet = (ROOT / "SUBMISSION.md").read_text(encoding="utf-8")

    for text in (readme, packet):
        assert "update_description" in text
        assert "save_document" in text
        assert "marks the column deprecated" not in text
