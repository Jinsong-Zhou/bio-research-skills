"""The note contract: what validation refuses, and what rendering shows.

Every check here corresponds to a way a note can read as better grounded than
it is. The validator cannot tell whether ``Table 2`` is the *right* table — it
can only insist that a table was named.
"""

import copy

import note
import pytest


def valid_note() -> dict:
    return {
        "language": "en",
        "paper": {"title": "A paper", "authors": ["A. One", "B. Two"], "fulltext": "full"},
        "understanding": {
            "problem": "Existing methods stall on long sequences.",
            "method": "A two-stage encoder.",
            "experiments": "PDBbind core set, three seeds, compared against AF3.",
            "findings": "12% higher Pearson r than the baseline (Table 2).",
        },
        "assessment": {
            "claims": [
                {
                    "claim": "12% higher Pearson r than AF3",
                    "evidence": "Table 2",
                    "confidence": "medium",
                    "issue": "single dataset",
                }
            ],
            "limitations": {"acknowledged": ["untested on membranes"], "unstated": []},
            "verdict": {
                "decision": "watch",
                "reasoning": "The gain rests on a self-reimplemented baseline.",
                "cost": "no code released",
                "next_steps": ["watch for a code release"],
            },
        },
        "relevance": {"status": "no-background-provided", "text": ""},
    }


def errors_for(mutate) -> list[str]:
    data = valid_note()
    mutate(data)
    return note.validate(data)[0]


class TestValidationAccepts:
    def test_a_complete_note_passes_clean(self):
        errors, warnings = note.validate(valid_note())
        assert errors == []
        assert warnings == []

    def test_the_shipped_template_fails_until_it_is_filled_in(self):
        """A skeleton that validated would be a skeleton that could be shipped."""
        errors, _ = note.validate(copy.deepcopy(note.TEMPLATE))
        assert errors

    @pytest.mark.parametrize(
        "evidence",
        [
            "Table 2", "Fig. 3b", "Figure 12", "Sec. 4.1", "Supplementary Fig. 7",
            "Extended Data Fig. 1", "p. 7", "Eq. 3", "Appendix B",
            "表 2", "图 3b", "第 4 节", "附录 B",
        ],
    )
    def test_anchors_are_recognised_in_both_languages(self, evidence):
        assert not errors_for(lambda d: d["assessment"]["claims"][0].update(evidence=evidence))


class TestValidationRefuses:
    @pytest.mark.parametrize("field", note.UNDERSTANDING_FIELDS)
    def test_an_empty_understanding_field(self, field):
        assert any(field in e for e in errors_for(lambda d: d["understanding"].update({field: ""})))

    def test_evidence_that_points_at_nothing_in_the_paper(self):
        """"The authors state that…" restates the claim; it does not support it."""
        errors = errors_for(
            lambda d: d["assessment"]["claims"][0].update(evidence="the authors state this")
        )
        assert any("figure, table, section or page" in e for e in errors)

    def test_a_claim_with_neither_evidence_nor_an_issue(self):
        errors = errors_for(
            lambda d: d["assessment"]["claims"][0].update(evidence=None, issue="")
        )
        assert any("neither evidence nor an issue" in e for e in errors)

    def test_no_claims_at_all(self):
        errors = errors_for(lambda d: d["assessment"].update(claims=[]))
        assert any("at least one claim" in e for e in errors)

    def test_a_verdict_without_reasoning(self):
        errors = errors_for(lambda d: d["assessment"]["verdict"].update(reasoning=""))
        assert any("coin flip" in e for e in errors)

    @pytest.mark.parametrize("decision", ["maybe", "", None, "Follow-up"])
    def test_a_decision_outside_the_three(self, decision):
        errors = errors_for(lambda d: d["assessment"]["verdict"].update(decision=decision))
        assert any("verdict.decision" in e for e in errors)

    def test_claiming_relevance_was_written_while_leaving_it_empty(self):
        errors = errors_for(lambda d: d["relevance"].update(status="written", text=""))
        assert any("relevance.text is empty" in e for e in errors)

    def test_an_invented_fulltext_state(self):
        errors = errors_for(lambda d: d["paper"].update(fulltext="partial"))
        assert any("paper.fulltext" in e for e in errors)


class TestNullEvidenceIsAFinding:
    def test_a_claim_the_paper_never_measures_is_accepted_when_it_says_so(self):
        """The most valuable row in the table is often this one."""
        errors = errors_for(
            lambda d: d["assessment"]["claims"][0].update(
                evidence=None, issue="asserted in the abstract; no experiment measures it"
            )
        )
        assert errors == []


class TestAbstractOnly:
    def _abstract_only(self, data):
        data["paper"]["fulltext"] = "abstract-only"

    def test_it_warns_rather_than_blocks(self):
        data = valid_note()
        self._abstract_only(data)
        errors, warnings = note.validate(data)
        assert errors == []
        assert any("abstract-only" in w for w in warnings)

    def test_evidence_anchors_are_not_demanded_of_a_note_written_without_the_paper(self):
        data = valid_note()
        self._abstract_only(data)
        data["assessment"]["claims"][0]["evidence"] = "stated in the abstract"
        assert note.validate(data)[0] == []

    def test_the_banner_leads_the_document(self):
        data = valid_note()
        self._abstract_only(data)
        rendered = note.render_markdown(data)
        assert "Abstract only" in rendered.split("##")[0]


class TestWarnings:
    def test_findings_with_no_pointer_warn_but_do_not_block(self):
        data = valid_note()
        data["understanding"]["findings"] = "The method works better."
        errors, warnings = note.validate(data)
        assert errors == []
        assert any("cites no figure" in w for w in warnings)


class TestRendering:
    def test_the_two_parts_and_the_claim_table_are_present(self):
        rendered = note.render_markdown(valid_note())
        assert "## Part 1" in rendered
        assert "## Part 2" in rendered
        assert "| Claim | Evidence | Confidence | Issue |" in rendered
        assert "| Table 2 |" in rendered

    def test_a_missing_relevance_section_says_it_was_left_empty_on_purpose(self):
        rendered = note.render_markdown(valid_note())
        assert "left empty rather than guessed at" in rendered

    def test_a_written_relevance_section_is_rendered(self):
        data = valid_note()
        data["relevance"] = {"status": "written", "text": "Directly overlaps your docking work."}
        assert "Directly overlaps your docking work." in note.render_markdown(data)

    def test_null_evidence_renders_as_a_visible_absence(self):
        data = valid_note()
        data["assessment"]["claims"][0].update(evidence=None, issue="never measured")
        rendered = note.render_markdown(data)
        assert "none given" in rendered

    def test_a_pipe_in_the_text_does_not_break_the_table(self):
        data = valid_note()
        data["assessment"]["claims"][0]["claim"] = "accuracy | precision both improve"
        row = [ln for ln in note.render_markdown(data).splitlines() if "both improve" in ln][0]
        assert "\\|" in row, "the pipe inside the text must be escaped"
        delimiters = row.count("|") - row.count("\\|")
        assert delimiters == 5, "an unescaped pipe would add a column"

    def test_a_newline_in_a_cell_does_not_break_the_table(self):
        data = valid_note()
        data["assessment"]["claims"][0]["issue"] = "first line\nsecond line"
        rendered = note.render_markdown(data)
        assert "first line second line" in rendered

    def test_chinese_headings_follow_the_language_field(self):
        data = valid_note()
        data["language"] = "zh"
        rendered = note.render_markdown(data)
        assert "第二部分" in rendered
        assert "| 主张 | 证据 | 可信度 | 问题 |" in rendered

    def test_an_unknown_language_falls_back_to_english_rather_than_crashing(self):
        data = valid_note()
        data["language"] = "de"
        assert "## Part 1" in note.render_markdown(data)

    def test_the_author_list_is_truncated_with_et_al(self):
        data = valid_note()
        data["paper"]["authors"] = ["A", "B", "C", "D", "E"]
        assert "A, B, C et al." in note.render_markdown(data)

    def test_three_authors_are_not_truncated(self):
        data = valid_note()
        data["paper"]["authors"] = ["A", "B", "C"]
        rendered = note.render_markdown(data)
        assert "A, B, C" in rendered and "et al." not in rendered

    def test_empty_limitation_lists_render_a_placeholder_not_a_gap(self):
        data = valid_note()
        data["assessment"]["limitations"] = {"acknowledged": [], "unstated": []}
        rendered = note.render_markdown(data)
        assert rendered.count("- —") == 2


class TestBlocks:
    """The renderer-agnostic form. Markdown is rendered *from* these, so a
    Markdown assertion elsewhere does not cover what a Word renderer sees."""

    KNOWN = {
        "title", "banner", "h1", "h2", "p", "note", "label",
        "fields", "bullets", "numbered", "table",
    }

    def test_every_block_has_a_known_type(self):
        types = {b["type"] for b in note.build_blocks(valid_note())}
        assert types <= self.KNOWN, f"unknown block types: {types - self.KNOWN}"

    def test_blocks_carry_no_markdown_markup(self):
        """Emphasis is the block type's job. Markup here would reach Word
        verbatim as literal asterisks."""
        import json

        data = valid_note()
        data["paper"]["fulltext"] = "abstract-only"
        data["relevance"] = {"status": "no-background-provided", "text": ""}
        dumped = json.dumps(note.build_blocks(data), ensure_ascii=False)
        for markup in ("**", "> ", "_No research", "`"):
            assert markup not in dumped, f"{markup!r} leaked into the blocks form"

    def test_the_claim_table_is_a_table_not_prose(self):
        table = [b for b in note.build_blocks(valid_note()) if b["type"] == "table"][0]
        assert len(table["header"]) == 4
        assert all(len(row) == 4 for row in table["rows"])

    def test_pipes_are_not_escaped_in_blocks(self):
        """Escaping is Markdown's problem; a Word cell holds the text verbatim."""
        data = valid_note()
        data["assessment"]["claims"][0]["claim"] = "accuracy | precision"
        table = [b for b in note.build_blocks(data) if b["type"] == "table"][0]
        assert table["rows"][0][0] == "accuracy | precision"

    def test_the_banner_appears_only_without_full_text(self):
        assert not [b for b in note.build_blocks(valid_note()) if b["type"] == "banner"]
        data = valid_note()
        data["paper"]["fulltext"] = "abstract-only"
        banner = [b for b in note.build_blocks(data) if b["type"] == "banner"][0]
        assert banner["lead"] and banner["text"]

    def test_an_empty_limitation_list_still_produces_a_visible_row(self):
        data = valid_note()
        data["assessment"]["limitations"] = {"acknowledged": [], "unstated": []}
        bullets = [b for b in note.build_blocks(data) if b["type"] == "bullets"]
        assert [b["items"] for b in bullets] == [["—"], ["—"]]

    def test_headings_follow_the_note_language(self):
        data = valid_note()
        data["language"] = "zh"
        headings = [b["text"] for b in note.build_blocks(data) if b["type"] == "h1"]
        assert "第二部分 — 评价" in headings

    def test_cost_is_a_labelled_paragraph_not_a_lone_bullet(self):
        blocks = note.build_blocks(valid_note())
        leads = [b.get("lead") for b in blocks if b["type"] == "p"]
        assert "Cost to act on it" in leads


class TestCli:
    def test_render_refuses_a_broken_note_by_default(self, tmp_path, capsys):
        import json

        broken = valid_note()
        broken["understanding"]["problem"] = ""
        path = tmp_path / "n.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        assert note.main(["render", str(path)]) == 1
        assert "refusing to render" in capsys.readouterr().err

    def test_force_renders_anyway(self, tmp_path):
        import json

        broken = valid_note()
        broken["understanding"]["problem"] = ""
        path = tmp_path / "n.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        assert note.main(["render", str(path), "--force", "-o", str(tmp_path / "n.md")]) == 0

    def test_validate_exits_nonzero_on_errors(self, tmp_path):
        import json

        path = tmp_path / "n.json"
        path.write_text(json.dumps(note.TEMPLATE), encoding="utf-8")
        assert note.main(["validate", str(path)]) == 1

    def test_a_file_that_is_not_json_fails_with_a_message(self, tmp_path, capsys):
        path = tmp_path / "n.json"
        path.write_text("not json", encoding="utf-8")
        assert note.main(["validate", str(path)]) == 2
        assert "cannot read" in capsys.readouterr().err
