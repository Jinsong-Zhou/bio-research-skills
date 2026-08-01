"""The record contract: what a finding must carry to be allowed to exist."""

from __future__ import annotations

import pytest
from _findings import (
    REQUIREMENT_KEYS,
    Evidence,
    Finding,
    FindingError,
    by_severity,
    read_json,
)

SOURCE = (Evidence(path="README.md", line=4, quote="a line"),)


class TestEvidence:
    def test_evidence_without_a_path_is_refused(self):
        with pytest.raises(FindingError, match="no source is a guess"):
            Evidence(path="", quote="something")

    def test_a_long_quote_is_clipped_rather_than_carried_whole(self):
        item = Evidence(path="a.sh", quote="x" * 500)
        assert len(item.to_dict()["quote"]) <= 200

    def test_whitespace_in_a_quote_is_collapsed(self):
        assert Evidence(path="a", quote="two   \n words").to_dict()["quote"] == "two words"

    def test_where_reads_as_a_clickable_location(self):
        assert Evidence(path="env/build.sh", line=12, quote="q").where() == "env/build.sh:12"

    def test_a_file_level_finding_has_no_line(self):
        assert Evidence(path=".github", quote="absent").where() == ".github"


class TestFinding:
    def test_a_finding_without_evidence_is_refused(self):
        """The rule the whole report rests on.

        "The weights look gated" is worth nothing next to
        "env/download.sh:412 fetches from huggingface.co". Enforcing it at
        construction means no check can quietly skip it.
        """
        with pytest.raises(FindingError, match="No evidence|no evidence"):
            Finding(id="x", layer="env", severity="note", summary="s", evidence=())

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("layer", "vibes", "unknown layer"),
            ("severity", "catastrophic", "unknown severity"),
        ],
    )
    def test_vocabulary_is_enforced(self, field, value, message):
        kwargs = {
            "id": "x",
            "layer": "env",
            "severity": "note",
            "summary": "s",
            "evidence": SOURCE,
            field: value,
        }
        with pytest.raises(FindingError, match=message):
            Finding(**kwargs)

    def test_an_unknown_target_is_refused(self):
        with pytest.raises(FindingError, match="unknown target"):
            Finding(
                id="x", layer="env", severity="note", summary="s", evidence=SOURCE,
                targets=("deployment",),
            )

    def test_a_finding_that_gates_nothing_is_refused(self):
        with pytest.raises(FindingError, match="gates nothing"):
            Finding(id="x", layer="env", severity="note", summary="s", evidence=SOURCE, targets=())

    def test_a_requirement_key_gate_cannot_read_is_refused_at_the_source(self):
        """Both ends guard this. Here is the writing end."""
        with pytest.raises(FindingError, match="unknown requirement key"):
            Finding(
                id="x", layer="hardware", severity="note", summary="s", evidence=SOURCE,
                requires={"tensor_cores": True},
            )

    @pytest.mark.parametrize("key", REQUIREMENT_KEYS)
    def test_every_declared_requirement_key_is_accepted(self, key):
        assert Finding(
            id="x", layer="hardware", severity="note", summary="s", evidence=SOURCE,
            requires={key: True},
        ).requires == {key: True}

    def test_gates_answers_per_target(self):
        item = Finding(
            id="x", layer="data", severity="blocking", summary="s", evidence=SOURCE,
            targets=("training",),
        )
        assert item.gates("training") is True
        assert item.gates("inference") is False


class TestRoundTrip:
    def test_a_finding_survives_json_and_back(self):
        original = Finding(
            id="weights.gated", layer="weights", severity="degraded", summary="gated",
            evidence=SOURCE, detail="why it matters", requires={"env_vars": ["HF_TOKEN"]},
        )
        restored = Finding.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.requires == original.requires
        assert restored.evidence[0].path == "README.md"
        assert restored.targets == original.targets

    def test_empty_optional_fields_stay_out_of_the_json(self):
        payload = Finding(
            id="x", layer="env", severity="note", summary="s", evidence=SOURCE
        ).to_dict()
        assert "detail" not in payload
        assert "requires" not in payload

    def test_a_finding_read_from_junk_does_not_explode_on_evidence(self):
        raw = {
            "id": "x", "layer": "env", "severity": "note",
            "summary": "s", "evidence": [{"path": "a"}],
        }
        assert Finding.from_dict(raw).evidence[0].line is None

    def test_a_requires_of_the_wrong_shape_is_refused_rather_than_dropped(self):
        """Dropping it takes a gate out of the report without saying so: the
        finding is still listed, and it is listed as met."""
        raw = {
            "id": "hardware.vram", "layer": "hardware", "severity": "note",
            "summary": "s", "evidence": [{"path": "a"}], "requires": ["vram_gb"],
        }
        with pytest.raises(FindingError) as caught:
            Finding.from_dict(raw)
        assert "hardware.vram" in str(caught.value)


class TestOrdering:
    def test_worst_first_then_layer(self):
        findings = [
            Finding(id="c", layer="env", severity="note", summary="n", evidence=SOURCE),
            Finding(id="a", layer="license", severity="blocking", summary="b", evidence=SOURCE),
            Finding(id="b", layer="weights", severity="degraded", summary="d", evidence=SOURCE),
        ]
        assert [f.id for f in by_severity(findings)] == ["a", "b", "c"]


class TestReadJson:
    def test_a_missing_file_names_itself(self, tmp_path):
        with pytest.raises(FindingError, match="survey.json"):
            read_json(str(tmp_path / "survey.json"))

    def test_a_json_array_is_refused_with_the_type_it_found(self, tmp_path):
        path = tmp_path / "survey.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(FindingError, match="found list"):
            read_json(str(path))
