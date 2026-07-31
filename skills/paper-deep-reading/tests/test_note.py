"""The note contract: what validation refuses, and what rendering shows.

Every check here corresponds to a way a note can read as better grounded than
it is. The validator cannot tell whether ``Table 2`` is the *right* table — it
can only insist that a table was named.
"""

import copy

import note
import pytest


def valid_note() -> dict:
    """A note that clears every check, including the depth floor.

    Written out at realistic length on purpose: a fixture of one-liners would
    let a regression in the depth warning pass unnoticed.
    """
    return {
        "language": "en",
        "paper": {
            "title": "A paper",
            "authors": ["A. One", "B. Two"],
            "type": "computational",
            "fulltext": "full",
        },
        "understanding": {
            "problem": (
                "Predicting binding affinity from a complex structure is hard because the "
                "training signal is scarce and biased: measured affinities exist for a few "
                "thousand complexes, overwhelmingly kinases and proteases. Earlier docking "
                "scores generalised badly because they were fitted on that same skewed slice, "
                "so a model could score well on held-out data and still fail on any target "
                "family the database under-represents."
            ),
            "approach": (
                "Two ideas, one per obstacle. Scarcity is answered by pre-training on "
                "unlabelled structures, so affinity prediction starts from a representation "
                "that already knows what contacts look like. Family bias is answered by "
                "reweighting the fine-tuning set per Pfam family. Nothing in the paper "
                "addresses the third obstacle, assay heterogeneity across source databases."
            ),
            "pipeline": (
                "Training: pre-train a contact-prediction head on PDB complexes deposited "
                "before 2021, then fine-tune on PDBbind with per-family reweighting, three "
                "seeds. Inference: supply a holo complex structure and get a scalar pKd; no "
                "MSA is needed at run time, which is the practical difference from the "
                "baseline. Evaluated on the PDBbind core set, n = 285 complexes."
            ),
            "mechanism": (
                "The reweighting is what carries the result. Removing it (Table 3) drops "
                "performance on under-represented families back to baseline while leaving "
                "kinase performance untouched, which is the signature of a bias correction "
                "rather than a general improvement. It should stop working wherever family "
                "labels are unreliable, and the paper does not test that regime."
            ),
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

    @pytest.mark.parametrize(
        "evidence",
        ["Fig. S3", "Table S1", "figure S2", "Figs. 2 and 3", "Tables S1-S3", "图 S3"],
    )
    def test_supplementary_citations_are_anchors_too(self, evidence):
        """``Fig. S3`` is how journals cite supplementary material, and
        SKILL.md step 3.6 sends the agent there because that is where a
        paper's weakest point usually sits. Demanding a digit immediately
        after the keyword rejected the ordinary way of writing it, leaving the
        agent to reword a correct citation or pass --force."""
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

    @pytest.mark.parametrize("kind", ["paper", "", None, "wet-lab"])
    def test_a_paper_type_outside_the_five(self, kind):
        """The type decides how the pipeline section decomposes, so a wrong one
        produces a section describing a paper nobody wrote."""
        errors = errors_for(lambda d: d["paper"].update(type=kind))
        assert any("paper.type" in e for e in errors)

    def test_an_empty_title(self):
        assert any("paper.title" in e for e in errors_for(lambda d: d["paper"].update(title="")))

    def test_an_empty_claim(self):
        errors = errors_for(lambda d: d["assessment"]["claims"][0].update(claim=""))
        assert any("claims[0].claim is empty" in e for e in errors)

    @pytest.mark.parametrize("confidence", ["quite sure", "", None, "High"])
    def test_a_confidence_outside_the_three(self, confidence):
        errors = errors_for(lambda d: d["assessment"]["claims"][0].update(confidence=confidence))
        assert any("claims[0].confidence" in e for e in errors)

    def test_a_claim_written_as_a_bare_string(self):
        errors = errors_for(lambda d: d["assessment"].update(claims=["Table 2 shows it"]))
        assert any("claims[0] must be an object" in e for e in errors)

    @pytest.mark.parametrize("value", ["none", None, {"a": 1}])
    def test_a_limitations_field_that_is_not_a_list(self, value):
        errors = errors_for(lambda d: d["assessment"]["limitations"].update(unstated=value))
        assert any("limitations.unstated must be a list" in e for e in errors)

    @pytest.mark.parametrize("status", ["pending", "", None, "Written"])
    def test_a_relevance_status_outside_the_two(self, status):
        errors = errors_for(lambda d: d["relevance"].update(status=status))
        assert any("relevance.status" in e for e in errors)


class TestTypeConfusion:
    """A string where a list belongs is the likeliest mistake in a note an
    agent writes by hand, and it used to be one the validator waved through
    into the Word document."""

    def test_authors_written_as_a_string(self):
        errors = errors_for(lambda d: d["paper"].update(authors="Zhou J, Li M"))
        assert any("paper.authors must be a list" in e for e in errors)

    def test_next_steps_written_as_a_string(self):
        """``list("watch for a release")`` is legal Python: this rendered as a
        twenty-item numbered list, one character per item."""
        errors = errors_for(
            lambda d: d["assessment"]["verdict"].update(next_steps="watch for a release")
        )
        assert any("next_steps must be a list" in e for e in errors)

    def test_a_list_holding_something_that_is_not_a_string(self):
        errors = errors_for(lambda d: d["paper"].update(authors=["A. One", {"name": "B"}]))
        assert any("paper.authors[1] must be a string" in e for e in errors)

    def test_an_understanding_field_written_as_a_list(self):
        """It rendered the Python repr — brackets, quotes and all — into the
        document, because ``str()`` never fails."""
        errors = errors_for(lambda d: d["understanding"].update(problem=["first", "second"]))
        assert any("understanding.problem must be a string" in e for e in errors)

    @pytest.mark.parametrize("section", ["paper", "understanding", "assessment", "relevance"])
    def test_a_whole_section_written_as_the_wrong_shape(self, section):
        errors = errors_for(lambda d: d.update({section: ["a list"]}))
        assert any(f"{section} must be an object" in e for e in errors)


class TestLanguage:
    @pytest.mark.parametrize("language", ["zh-CN", "中文", "ZH", "Chinese"])
    def test_a_language_outside_the_table_is_rejected(self, language):
        """``STRINGS.get(lang, STRINGS["en"])`` means an unrecognised value
        renders an entirely English scaffold around Chinese content, and
        nothing says so. It was the only vocabulary never validated."""
        errors = errors_for(lambda d: d.update(language=language))
        assert any("language must be one of" in e for e in errors)

    def test_an_absent_language_defaults_to_english(self):
        data = valid_note()
        del data["language"]
        assert note.validate(data)[0] == []
        assert "## Part 1" in note.render_markdown(data)

    def test_both_heading_tables_carry_the_same_keys(self):
        """A key added to one and not the other is a KeyError at render time,
        in whichever language nobody tested."""
        assert note.STRINGS["en"].keys() == note.STRINGS["zh"].keys()


class TestDepthFloor:
    """A one-line field means the paper was summarised, not read."""

    @pytest.mark.parametrize("field", note.DEPTH_FIELDS)
    def test_a_one_liner_warns(self, field):
        data = valid_note()
        data["understanding"][field] = "The method improves accuracy."
        errors, warnings = note.validate(data)
        assert errors == []
        assert any(f"understanding.{field}" in w and "weighted characters" in w for w in warnings)

    def test_findings_may_legitimately_be_short(self):
        """A headline number is a result; it does not need three paragraphs."""
        data = valid_note()
        data["understanding"]["findings"] = "12% higher Pearson r (Table 2)."
        assert note.validate(data)[1] == []

    def test_chinese_clears_the_floor_at_half_the_characters(self):
        """CJK counts double, so one threshold serves both languages rather
        than demanding three English paragraphs or one Chinese line."""
        english = "a" * 179
        chinese = "问" * 90
        assert note._depth(english) < note.MIN_DEPTH
        assert note._depth(chinese) >= note.MIN_DEPTH


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


class TestNoMarkupInTheNote:
    """Markdown renders it; Word shows it verbatim. Since both come from one
    note, anything working in only one of them is a defect."""

    @pytest.mark.parametrize(
        ("text", "what"),
        [
            ("the **key** step", "bold"),
            ("the __key__ step", "underscore bold"),
            ("call `note.py` first", "backticks"),
            ("# A heading\nthen text", "heading"),
            ("> a quotation\nthen text", "blockquote"),
            ("- first item\n- second item", "bullets"),
            ("see [the repo](https://example.invalid)", "a link"),
            ("| a | b |\n|---|---|\n| 1 | 2 |", "a pipe table"),
            ("the <b>key</b> step", "an HTML tag"),
        ],
    )
    def test_markup_anywhere_is_rejected(self, text, what):
        errors = errors_for(lambda d: d["understanding"].update(problem=text))
        assert any("understanding.problem" in e for e in errors), what

    @pytest.mark.parametrize(
        "text",
        [
            "升高约两倍 (***, p < 0.001)，但 n = 3。",
            "与对照相比显著 (**, p<0.01) 而处理组 (***, p<0.001) 更强。",
            "***p < 0.001, **p < 0.01, *p < 0.05 (Fig. 2c)",
            "效应在处理组消失 (**)。",
            "两组差异见 Fig. 2c。\n***p < 0.001, **p < 0.01",
        ],
    )
    def test_significance_stars_are_statistics_not_bold(self, text):
        """Every experimental paper reports its p-values this way. Treating a
        bare run of asterisks as Markdown meant rejecting the domain — and the
        agent's only escape was to reword a correct result or pass --force.

        Matching balanced emphasis directly cannot fix this: in the second
        case a paired pattern bridges the two runs and calls the text between
        them bold. The runs are cleared first instead."""
        assert errors_for(lambda d: d["understanding"].update(problem=text)) == []

    @pytest.mark.parametrize("text", ["效应量 **翻倍** 了", "the **key** step", "a __key__ step"])
    def test_real_emphasis_is_still_rejected(self, text):
        """The significance carve-out must not open a hole: paired delimiters
        around text are still Markdown and still reach Word verbatim."""
        errors = errors_for(lambda d: d["understanding"].update(problem=text))
        assert any("bold markers" in e for e in errors)

    def test_it_reaches_nested_text(self):
        errors = errors_for(lambda d: d["assessment"]["claims"][0].update(issue="**no rescue**"))
        assert any("assessment.claims[0].issue" in e for e in errors)

    def test_it_reaches_list_items(self):
        errors = errors_for(
            lambda d: d["assessment"]["limitations"].update(unstated=["ok", "**not ok**"])
        )
        assert any("limitations.unstated[1]" in e for e in errors)

    def test_ordinary_prose_with_asterisk_free_punctuation_passes(self):
        """Em dashes, brackets, ratios and DOIs must not trip the check."""
        errors = errors_for(
            lambda d: d["understanding"].update(
                problem="A 2-fold change (Fig. 1) — see 10.1101/x — is within noise; n = 3/group. "
                * 3
            )
        )
        assert errors == []


class TestParagraphSplitting:
    def test_a_blank_line_starts_a_new_block(self):
        """Word has no newline inside a paragraph, so a renderer handed the
        raw string produces one run-on wall of text."""
        data = valid_note()
        data["understanding"]["problem"] = "First para.\n\nSecond para.\n\nThird para."
        texts = [b["text"] for b in note.build_blocks(data) if b["type"] == "p"]
        assert "First para." in texts
        assert "Second para." in texts
        assert "Third para." in texts

    def test_single_newlines_stay_within_one_paragraph(self):
        assert note.paragraphs("a\nb") == ["a\nb"]

    def test_blank_lines_of_whitespace_still_split(self):
        assert note.paragraphs("a\n   \nb") == ["a", "b"]

    def test_empty_text_yields_no_blocks(self):
        assert note.paragraphs("") == []


class TestBlocks:
    """The renderer-agnostic form. Markdown is rendered *from* these, so a
    Markdown assertion elsewhere does not cover what a Word renderer sees."""

    def test_every_block_has_a_known_type(self):
        data = valid_note()
        data["paper"]["fulltext"] = "abstract-only"
        types = {b["type"] for b in note.build_blocks(data)}
        known = set(note.BLOCK_TYPES)
        assert types <= known, f"unknown block types: {types - known}"

    def test_the_declared_vocabulary_is_the_one_actually_emitted(self):
        """BLOCK_TYPES is what SKILL.md, note-schema.md and the external Word
        renderer are all written against; a name in it that nothing emits is
        as misleading as one missing from it."""
        data = valid_note()
        data["paper"]["fulltext"] = "abstract-only"
        assert {b["type"] for b in note.build_blocks(data)} == set(note.BLOCK_TYPES)

    def test_markdown_refuses_a_block_type_it_does_not_handle(self, monkeypatch):
        """The dispatch had no else, so a type added to build_blocks and
        forgotten here vanished from the Markdown with no error, no warning
        and a zero exit — the two renderings silently disagreeing about what
        the document contains."""
        monkeypatch.setattr(
            note, "build_blocks", lambda _: [{"type": "callout", "text": "vanishes"}]
        )
        with pytest.raises(note.NoteError, match="unhandled block type"):
            note.render_markdown(valid_note())

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

    def test_force_renders_anyway_and_writes_the_file(self, tmp_path):
        import json

        broken = valid_note()
        broken["understanding"]["problem"] = ""
        path = tmp_path / "n.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        out = tmp_path / "n.md"
        assert note.main(["render", str(path), "--force", "-o", str(out)]) == 0
        assert "Part 2" in out.read_text(encoding="utf-8")

    def test_force_still_reports_every_error_it_overrides(self, tmp_path, capsys):
        """The help text used to promise "the gaps stay visible in the output"
        while _report([], warnings) dropped the errors on the floor: an empty
        section rendered as a bare heading the reader skims past, and the exit
        code was 0."""
        import json

        broken = valid_note()
        broken["understanding"]["mechanism"] = ""
        broken["paper"]["type"] = "bogus"
        path = tmp_path / "n.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        assert note.main(["render", str(path), "--force", "-o", str(tmp_path / "n.md")]) == 0
        err = capsys.readouterr().err
        assert "overridden: understanding.mechanism is empty" in err
        assert "overridden: paper.type" in err
        assert "2 unfixed error(s)" in err

    def test_force_renders_a_malformed_note_instead_of_crashing(self, tmp_path):
        """--force is the mode most likely to be handed a note of the wrong
        shape, and it was the one that died on it."""
        import json

        path = tmp_path / "n.json"
        path.write_text(
            json.dumps(
                {
                    "paper": {"title": "T", "type": "theory", "fulltext": "full"},
                    "assessment": {
                        "claims": ["a bare string claim"],
                        "verdict": {"decision": "skip", "reasoning": "r", "next_steps": "one step"},
                    },
                }
            ),
            encoding="utf-8",
        )
        out = tmp_path / "n.md"
        assert note.main(["render", str(path), "--force", "-o", str(out)]) == 0
        rendered = out.read_text(encoding="utf-8")
        assert "a bare string claim" in rendered
        assert "1. one step" in rendered, "a string must not explode into one item per character"

    def test_validate_reports_a_malformed_note_instead_of_crashing(self, tmp_path, capsys):
        import json

        path = tmp_path / "n.json"
        path.write_text(json.dumps({"paper": "A paper", "understanding": ["problem"]}), "utf-8")
        assert note.main(["validate", str(path)]) == 1
        assert "must be an object" in capsys.readouterr().err

    def test_validate_exits_nonzero_on_errors(self, tmp_path):
        import json

        path = tmp_path / "n.json"
        path.write_text(json.dumps(note.TEMPLATE), encoding="utf-8")
        assert note.main(["validate", str(path)]) == 1

    def test_validate_says_ok_and_exits_zero_on_a_clean_note(self, tmp_path, capsys):
        import json

        path = tmp_path / "n.json"
        path.write_text(json.dumps(valid_note()), encoding="utf-8")
        assert note.main(["validate", str(path)]) == 0
        assert "ok" in capsys.readouterr().err

    def test_blocks_format_emits_json_not_markdown(self, tmp_path, capsys):
        import json

        path = tmp_path / "n.json"
        path.write_text(json.dumps(valid_note()), encoding="utf-8")
        assert note.main(["render", str(path), "--format", "blocks"]) == 0
        blocks = json.loads(capsys.readouterr().out)
        assert blocks[0]["type"] == "title"

    def test_template_prints_a_note_that_does_not_yet_validate(self, capsys):
        import json

        assert note.main(["template"]) == 0
        assert note.validate(json.loads(capsys.readouterr().out))[0]

    def test_a_file_that_is_not_json_fails_with_a_message(self, tmp_path, capsys):
        path = tmp_path / "n.json"
        path.write_text("not json", encoding="utf-8")
        assert note.main(["validate", str(path)]) == 2
        assert "cannot read" in capsys.readouterr().err

    def test_a_top_level_json_array_is_not_a_note(self, tmp_path, capsys):
        path = tmp_path / "n.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert note.main(["validate", str(path)]) == 2
        assert "not a JSON object" in capsys.readouterr().err

    def test_a_note_saved_in_another_encoding_fails_with_a_message(self, tmp_path, capsys):
        """UnicodeDecodeError is a ValueError, not an OSError, so a note saved
        as UTF-16 used to come out as a raw traceback."""
        path = tmp_path / "n.json"
        path.write_bytes('{"language": "zh"}'.encode("utf-16"))
        assert note.main(["validate", str(path)]) == 2
        assert "cannot read" in capsys.readouterr().err
