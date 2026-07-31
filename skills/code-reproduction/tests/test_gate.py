"""The verdict logic — where a wrong answer costs someone a day."""

from __future__ import annotations

import json

import gate as gate_module
import pytest
from _findings import FindingError
from gate import assess, evaluate, python_satisfies, render_markdown


def host(**overrides):
    base = {
        "schema": "code-reproduction/probe/1",
        "platform": {"os": "linux", "machine": "x86_64", "glibc": "2.35"},
        "python": {"short": "3.12"},
        "gpu": {
            "available": True,
            "vram_gb": 80.0,
            "cuda": "12.6",
            "count": 1,
            "devices": [{"name": "H100", "vram_gb": 80.0}],
        },
        "disk": {"free_gb": 500.0, "path": "/data"},
        "tools": {},
        "env_vars": {},
        "reachability": {},
    }
    base.update(overrides)
    return base


def a_finding(**overrides):
    base = {
        "id": "env.thing",
        "layer": "env",
        "severity": "note",
        "summary": "a thing",
        "targets": ["inference", "training"],
        "evidence": [{"path": "README.md", "line": 3, "quote": "the line"}],
    }
    base.update(overrides)
    return base


def a_survey(findings, inconclusive=None):
    return {
        "schema": "code-reproduction/survey/1",
        "repo": {"name": "repo", "root": "/repo", "git": {"commit": "abc1234"}},
        "findings": findings,
        "inconclusive": inconclusive or [],
    }


class TestFailClosed:
    """Each of these produced a clean report, and all of them are one mistake.

    Something the program could not evaluate came out the far end as something
    it had evaluated and found fine. That is the failure this skill exists to
    name, which makes it the one worth testing hardest.
    """

    def test_the_survey_and_probe_arguments_swapped_are_refused(self):
        """`--survey probe.json --probe survey.json` used to print OK and exit 0."""
        with pytest.raises(FindingError) as caught:
            assess(host(), a_survey([a_finding()]))
        assert "swapped" in str(caught.value)

    def test_a_file_that_declares_no_schema_is_refused(self):
        survey = a_survey([a_finding()])
        del survey["schema"]
        with pytest.raises(FindingError):
            assess(survey, host())

    def test_a_survey_that_found_nothing_at_all_is_unknown(self):
        """No findings is a survey that did not run, not a repository with no demands.
        Every real checkout trips at least the handoff checks."""
        assert assess(a_survey([]), host())["verdict"] == "unknown"

    def test_a_typo_in_a_verdict_literal_is_refused(self):
        """`_worst` scans for the verdicts it knows and ignores the rest, so one
        mistyped literal among the twenty in `evaluate` would read as a pass."""
        with pytest.raises(FindingError) as caught:
            gate_module._gate("vram_gb", 40.0, 24.0, "blockd", "a typo")
        assert "blockd" in str(caught.value)

    def test_nothing_to_compare_is_not_a_pass(self):
        assert gate_module._worst([]) == "unknown"


class TestUnknownOutranksDegraded:
    """The ordering the rest of this file rests on, finally pinned.

    Swapping the two middle entries of `VERDICTS` left all 137 other tests
    passing. A report that says "you can start, with known problems" when a
    stated 40 GB requirement was measured against a card nobody could read is
    the precise thing `unknown` exists to prevent.
    """

    def test_worst_prefers_unknown_to_degraded(self):
        assert gate_module._worst(["degraded", "unknown"]) == "unknown"

    def test_an_unreadable_gate_outranks_a_known_problem(self):
        report = assess(
            a_survey(
                [
                    a_finding(id="env.torch-outside-manifest", severity="degraded"),
                    a_finding(id="hardware.vram", requires={"vram_gb": 40.0}),
                ]
            ),
            host(gpu={"available": True, "vram_gb": None, "cuda": "12.6"}),
        )
        assert report["verdict"] == "unknown"

    def test_an_open_question_from_the_survey_outranks_a_known_problem(self):
        report = assess(
            a_survey(
                [a_finding(severity="degraded")],
                inconclusive=[{"check": "files.unread", "why": "build.sh could not be read"}],
            ),
            host(),
        )
        assert report["verdict"] == "unknown"


class TestSingleRequirements:
    def test_a_missing_gpu_blocks_and_repeats_the_probe_reason(self):
        gpu = {"available": False, "why": "nvidia-smi is not on PATH", "vram_gb": None}
        result = evaluate("gpu", True, host(gpu=gpu))
        assert result["verdict"] == "blocked"
        assert "nvidia-smi" in result["why"]

    def test_an_absent_gpu_section_is_unknown_not_blocked(self):
        """A probe that never reported is not a probe that reported nothing."""
        assert evaluate("gpu", True, host(gpu={}))["verdict"] == "unknown"

    @pytest.mark.parametrize(
        ("have", "need", "expected"),
        [(80.0, 24.0, "ok"), (24.0, 24.0, "ok"), (16.0, 24.0, "blocked"), (None, 24.0, "unknown")],
    )
    def test_vram(self, have, need, expected):
        gpu = {"available": True, "vram_gb": have, "cuda": "12.6"}
        assert evaluate("vram_gb", need, host(gpu=gpu))["verdict"] == expected

    @pytest.mark.parametrize(
        ("free", "need", "expected"),
        [(500.0, 100.0, "ok"), (50.0, 100.0, "blocked"), (None, 100.0, "unknown")],
    )
    def test_disk(self, free, need, expected):
        assert evaluate("disk_gb", need, host(disk={"free_gb": free}))["verdict"] == expected

    def test_an_os_mismatch_blocks(self):
        result = evaluate("os", "linux", host(platform={"os": "darwin"}))
        assert result["verdict"] == "blocked"
        assert "darwin" in result["why"]

    def test_glibc_below_the_floor_blocks(self):
        probe = host(platform={"glibc": "2.31"})
        assert evaluate("glibc_min", "2.35", probe)["verdict"] == "blocked"

    def test_no_glibc_reported_is_unknown(self):
        probe = host(platform={"os": "darwin"})
        assert evaluate("glibc_min", "2.35", probe)["verdict"] == "unknown"

    def test_a_coarser_version_than_the_question_does_not_pass_it(self):
        """CUDA "12" against a 12.6 floor has not answered the question."""
        gpu = {"available": True, "cuda": "12", "vram_gb": 80.0}
        assert evaluate("cuda_min", "12.6", host(gpu=gpu))["verdict"] == "blocked"

    def test_a_newer_cuda_passes(self):
        gpu = {"available": True, "cuda": "12.8", "vram_gb": 80.0}
        assert evaluate("cuda_min", "12.6", host(gpu=gpu))["verdict"] == "ok"


class TestCredentialsAndHosts:
    def test_an_unset_credential_blocks(self):
        probe = host(env_vars={"HF_TOKEN": {"set": False}})
        result = evaluate("env_vars", ["HF_TOKEN"], probe)
        assert result["verdict"] == "blocked"
        assert "HF_TOKEN" in result["why"]

    def test_a_credential_nobody_checked_is_unknown_not_missing(self):
        """"Not probed" and "probed and absent" are different facts.

        Collapsing them means a probe run without `--from-survey` reports
        every credential as missing, and the report cries wolf; collapsing
        them the other way is worse.
        """
        result = evaluate("env_vars", ["HF_TOKEN"], host(env_vars={}))
        assert result["verdict"] == "unknown"
        assert "not checked" in result["why"]

    def test_all_credentials_present_passes(self):
        probe = host(env_vars={"HF_TOKEN": {"set": True}, "NGC_API_KEY": {"set": True}})
        assert evaluate("env_vars", ["HF_TOKEN", "NGC_API_KEY"], probe)["verdict"] == "ok"

    def test_an_unreachable_host_blocks(self):
        probe = host(reachability={"huggingface.co": {"reachable": False}})
        assert evaluate("network_hosts", ["huggingface.co"], probe)["verdict"] == "blocked"

    def test_an_untested_host_is_unknown(self):
        assert evaluate("network_hosts", ["huggingface.co"], host())["verdict"] == "unknown"


class TestUnknownRequirements:
    def test_gate_refuses_a_requirement_it_has_no_rule_for(self):
        """A requirement key gate.py cannot evaluate must not pass silently.

        The failure this prevents: someone adds a key to REQUIREMENT_KEYS,
        survey.py starts emitting it, and gate.py — had it fallen through —
        would report a repository as clear on a requirement nobody checked.
        """
        with pytest.raises(FindingError, match="no rule for requirement"):
            evaluate("gpu_architecture", "sm_90", host())

    def test_a_finding_cannot_carry_an_unknown_requirement_key(self):
        with pytest.raises(FindingError, match="unknown requirement key"):
            assess(a_survey([a_finding(requires={"quantum": True})]), host())


class TestSeverityAndGatesTogether:
    def test_a_degraded_finding_stays_degraded_when_its_gate_passes(self):
        """Reaching the login page does not remove the login page.

        The gate answers "can this host get there". The finding says "there
        is a wall". Letting the passing gate overwrite the severity produced
        a report that filed a credential-gated download under "met".
        """
        item = a_finding(
            id="weights.gated.hf",
            layer="weights",
            severity="degraded",
            requires={"network_hosts": ["huggingface.co"]},
        )
        probe = host(reachability={"huggingface.co": {"reachable": True}})
        report = assess(a_survey([item]), probe)
        assert report["findings"][0]["verdict"] == "degraded"

    def test_a_blocking_finding_with_nothing_to_check_still_blocks(self):
        item = a_finding(id="license.restricted.weights", layer="license", severity="blocking")
        assert assess(a_survey([item]), host())["verdict"] == "blocked"

    def test_a_note_with_nothing_to_check_is_ok(self):
        assert assess(a_survey([a_finding()]), host())["verdict"] == "ok"

    def test_the_overall_verdict_is_the_worst_one(self):
        findings = [
            a_finding(id="a", requires={"vram_gb": 24.0}),
            a_finding(id="b", requires={"vram_gb": 200.0}),
        ]
        assert assess(a_survey(findings), host())["verdict"] == "blocked"

    def test_an_unresolved_survey_check_keeps_the_verdict_out_of_ok(self):
        """Gaps in the survey have to reach the verdict, not just the appendix."""
        report = assess(
            a_survey([a_finding()], inconclusive=[{"check": "hardware.vram", "why": "not stated"}]),
            host(),
        )
        assert report["verdict"] == "unknown"


class TestTargets:
    def test_training_only_findings_do_not_gate_inference(self):
        item = a_finding(
            id="data.accession-lists", layer="data", severity="blocking", targets=["training"]
        )
        report = assess(a_survey([item]), host(), target="inference")
        assert report["verdict"] == "ok"
        assert [d["id"] for d in report["deferred"]] == ["data.accession-lists"]

    def test_the_same_finding_blocks_the_training_target(self):
        item = a_finding(
            id="data.accession-lists", layer="data", severity="blocking", targets=["training"]
        )
        assert assess(a_survey([item]), host(), target="training")["verdict"] == "blocked"

    def test_the_report_says_out_loud_what_it_did_not_evaluate(self):
        item = a_finding(id="d", targets=["training"], summary="rebuild the dataset")
        text = render_markdown(assess(a_survey([item]), host(), target="inference"))
        assert "says nothing about training" in text
        assert "--target training" in text


class TestPythonSpecifiers:
    @pytest.mark.parametrize(
        ("spec", "version", "expected"),
        [
            (">=3.12", "3.12", True),
            (">=3.12", "3.9", False),
            (">=3.9,<3.13", "3.11", True),
            (">=3.9,<3.13", "3.13", False),
            ("==3.10.*", "3.10", True),
            ("==3.10.*", "3.11", False),
            ("~=3.11", "3.11", True),
            ("~=3.11", "4.0", False),
            (">3.8", "3.9", True),
            ("!=3.10", "3.10", False),
        ],
    )
    def test_specifiers(self, spec, version, expected):
        assert python_satisfies(spec, version) is expected

    def test_an_unparseable_specifier_is_none_rather_than_true(self):
        """Unreadable is not satisfied."""
        assert python_satisfies("whatever the authors meant", "3.12") is None

    def test_an_unparseable_specifier_reaches_the_report_as_unknown(self):
        item = a_finding(requires={"python": "≥ 3.12"})
        assert assess(a_survey([item]), host())["verdict"] == "unknown"


class TestReport:
    def test_the_headline_carries_the_verdict_and_the_target(self):
        text = render_markdown(assess(a_survey([a_finding()]), host(), target="inference"))
        assert "**Verdict: OK** (inference)" in text

    def test_blocked_findings_print_their_evidence(self):
        item = a_finding(
            requires={"vram_gb": 200.0},
            evidence=[{"path": "docs/hardware.md", "line": 12, "quote": "200 GB VRAM"}],
        )
        text = render_markdown(assess(a_survey([item]), host()))
        assert "`docs/hardware.md:12`" in text

    def test_checked_requirements_and_bare_remarks_are_listed_apart(self):
        """"Met" must mean something was verified, not that nothing was asked."""
        findings = [
            a_finding(
                id="env.python",
                summary="Requires Python >=3.12",
                requires={"python": ">=3.12"},
            ),
            a_finding(id="handoff.no-ci", layer="handoff", summary="No CI configuration"),
        ]
        text = render_markdown(assess(a_survey(findings), host()))
        met = text.split("## Checked and met")[1].split("##")[0]
        worth = text.split("## Worth knowing")[1].split("##")[0]
        assert "Requires Python >=3.12" in met
        assert "No CI configuration" in worth
        assert "No CI configuration" not in met

    def test_repo_shipped_instructions_are_called_out_before_the_gates(self):
        findings = [
            a_finding(
                id="handoff.repo-ships-guidance",
                layer="handoff",
                summary="ships its own agent instructions",
                evidence=[{"path": ".claude/skills/setup/SKILL.md", "line": 1, "quote": "x"}],
            ),
            a_finding(id="z", requires={"vram_gb": 200.0}),
        ]
        text = render_markdown(assess(a_survey(findings), host()))
        assert text.index("Read the repository's own instructions first") < text.index("## Blocked")


class TestCommandLine:
    def _files(self, tmp_path, findings, probe):
        survey_path = tmp_path / "survey.json"
        probe_path = tmp_path / "probe.json"
        survey_path.write_text(json.dumps(a_survey(findings)), encoding="utf-8")
        probe_path.write_text(json.dumps(probe), encoding="utf-8")
        return ["--survey", str(survey_path), "--probe", str(probe_path)]

    def test_a_blocked_verdict_exits_non_zero(self, tmp_path, capsys):
        args = self._files(tmp_path, [a_finding(requires={"vram_gb": 200.0})], host())
        assert gate_module.main(args) == 1
        assert "BLOCKED" in capsys.readouterr().out

    def test_an_unknown_verdict_also_exits_non_zero(self, tmp_path, capsys):
        args = self._files(tmp_path, [a_finding(requires={"cuda_min": "12.6"})], host(gpu={}))
        assert gate_module.main(args) == 1
        capsys.readouterr()

    def test_a_clear_verdict_exits_zero(self, tmp_path, capsys):
        args = self._files(tmp_path, [a_finding(requires={"vram_gb": 24.0})], host())
        assert gate_module.main(args) == 0
        capsys.readouterr()

    def test_an_unreadable_survey_says_which_file(self, tmp_path, capsys):
        probe_path = tmp_path / "probe.json"
        probe_path.write_text("{}", encoding="utf-8")
        code = gate_module.main(
            ["--survey", str(tmp_path / "missing.json"), "--probe", str(probe_path)]
        )
        assert code == 2
        assert "missing.json" in capsys.readouterr().err

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        args = self._files(tmp_path, [a_finding()], host())
        gate_module.main([*args, "--format", "json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == gate_module.SCHEMA
        assert payload["target"] == "inference"
