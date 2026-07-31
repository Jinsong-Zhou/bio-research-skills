"""What the survey must notice, and what it must not invent."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
import survey as survey_module
from survey import survey_repo

APACHE = """
                                 Apache License
                           Version 2.0, January 2004

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
"""

MIT = """MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""

# Condensed from the opening of the NVIDIA Open Model License. It contains the
# word "commercial" and grants commercial use — the shape of text a naive
# keyword scan misreads as a restriction.
NVIDIA_OPEN_MODEL = """NVIDIA Open Model License Agreement

NVIDIA models released under this Agreement are intended to be used
permissively and enable the further development of AI technologies.

- Models are commercially usable.
- You are free to create and distribute Derivative Models.
"""

RESEARCH_ONLY = """Model Weights License

The weights are made available for research purposes only. Any commercial
application requires a separate agreement with the authors.
"""


def ids_of(payload) -> set[str]:
    return {finding["id"] for finding in payload["findings"]}


def finding(payload, finding_id):
    for item in payload["findings"]:
        if item["id"] == finding_id:
            return item
    raise AssertionError(f"{finding_id} not found; got {sorted(ids_of(payload))}")


def inconclusive_checks(payload) -> set[str]:
    return {item["check"] for item in payload["inconclusive"]}


class TestLicenseLayers:
    def test_a_root_licence_that_only_forwards_is_reported(self, make_repo):
        root = make_repo(
            {
                "LICENSE": "This repository contains multiple components covered by "
                "different licenses.\n\nSee the licenses/ directory for details.\n",
                "licenses/license_code.txt": APACHE,
                "licenses/license_weights.txt": NVIDIA_OPEN_MODEL,
            }
        )
        payload = survey_repo(root)
        assert "license.root-is-a-pointer" in ids_of(payload)

    def test_a_root_licence_with_real_terms_is_not_reported_as_a_pointer(self, make_repo):
        root = make_repo({"LICENSE": MIT})
        assert "license.root-is-a-pointer" not in ids_of(survey_repo(root))

    def test_the_layers_are_read_separately(self, make_repo):
        root = make_repo(
            {
                "LICENSE": APACHE,
                "licenses/license_weights.txt": NVIDIA_OPEN_MODEL,
                "licenses/license_datasets.txt": "Creative Commons Attribution 4.0 International",
            }
        )
        detail = finding(survey_repo(root), "license.layers-differ")["detail"]
        assert "weights → NVIDIA Open Model License" in detail
        assert "data → CC-BY-4.0" in detail

    def test_one_licence_covering_everything_is_not_a_layer_conflict(self, make_repo):
        root = make_repo({"LICENSE": MIT})
        assert "license.layers-differ" not in ids_of(survey_repo(root))

    def test_research_only_weights_block_regardless_of_the_code_licence(self, make_repo):
        root = make_repo({"LICENSE": APACHE, "licenses/model_weights_license.txt": RESEARCH_ONLY})
        item = finding(survey_repo(root), "license.restricted.weights")
        assert item["severity"] == "blocking"
        assert "research purposes only" in item["evidence"][0]["quote"]

    def test_the_nvidia_open_model_licence_is_not_called_non_commercial(self, make_repo):
        """A licence that grants commercial use must not be flagged as forbidding it.

        This is the false positive that would make the whole licence layer
        untrustworthy: it says "commercially usable" and mentions commerce
        throughout, and any check keyed on the bare word "commercial" reports
        the opposite of what it says.
        """
        root = make_repo({"LICENSE": APACHE, "licenses/license_weights.txt": NVIDIA_OPEN_MODEL})
        assert not [i for i in ids_of(survey_repo(root)) if i.startswith("license.restricted")]

    def test_an_aggregate_third_party_file_reports_every_licence_in_it(self, make_repo):
        """`license_third_party.txt` stacks several licences; the first is not the answer."""
        root = make_repo(
            {
                "LICENSE": APACHE,
                "licenses/license_weights.txt": NVIDIA_OPEN_MODEL,
                "licenses/license_third_party.txt": APACHE
                + "\n"
                + MIT
                + '\n"THE BEER-WARE LICENSE" (Revision 42)\n',
            }
        )
        summary = finding(survey_repo(root), "license.vendored-differs")["summary"]
        assert "Beer-ware" in summary
        assert "MIT" in summary

    def test_a_vendored_licence_is_not_reported_as_a_layer_conflict(self, make_repo):
        """Nearly every repository copies in something under other terms.

        Counting that as "this project's licences differ" fires on almost
        everything and teaches the reader to skip the finding — so the one
        repository where code and weights genuinely diverge gets skipped too.
        """
        root = make_repo(
            {"LICENSE": APACHE, "ProteinMPNN/LICENSE": MIT, "openfold/LICENSE": APACHE}
        )
        payload = survey_repo(root)
        assert "license.layers-differ" not in ids_of(payload)
        assert "MIT" in finding(payload, "license.vendored-differs")["summary"]

    def test_a_single_repository_licence_is_not_called_the_code_licence(self, make_repo):
        """One LICENSE covering source, weights and data is not "the code licence"."""
        root = make_repo({"LICENSE": APACHE + "\n" + RESEARCH_ONLY, "ProteinMPNN/LICENSE": MIT})
        assert "license.restricted.repository" in ids_of(survey_repo(root))

    def test_no_licence_at_all_is_blocking(self, make_repo):
        payload = survey_repo(make_repo({"README.md": "# tool\n", "main.py": "print(1)\n"}))
        assert finding(payload, "license.absent")["severity"] == "blocking"

    def test_an_unrecognised_licence_is_recorded_as_unread_not_as_absent(self, make_repo):
        root = make_repo({"LICENSE": "Bespoke terms nobody has seen before.\n"})
        assert "license.identify" in inconclusive_checks(survey_repo(root))


class TestSilentInstallFailures:
    def test_an_install_step_that_discards_its_failure_is_reported(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "env/build.sh": (
                    "#!/bin/bash\nset -e\n"
                    'uv pip install "atomworks[ml]" || echo "Warning: atomworks install failed"\n'
                    'uv pip install "git+https://example.com/tmol.git" || true\n'
                ),
            }
        )
        item = finding(survey_repo(root), "env.swallowed-install-failure")
        assert item["severity"] == "degraded"
        assert len(item["evidence"]) == 2

    def test_an_install_step_that_fails_loudly_is_not_reported(self, make_repo):
        root = make_repo(
            {"LICENSE": MIT, "env/build.sh": "#!/bin/bash\nset -e\nuv pip install torch\n"}
        )
        assert "env.swallowed-install-failure" not in ids_of(survey_repo(root))

    def test_a_bare_redirect_on_a_non_install_line_is_not_reported(self, make_repo):
        root = make_repo(
            {"LICENSE": MIT, "env/build.sh": "#!/bin/bash\nwhich nvidia-smi 2>/dev/null\n"}
        )
        assert "env.swallowed-install-failure" not in ids_of(survey_repo(root))


class TestFixesThatLiveOnlyInProse:
    KNOWN_ISSUE = (
        "## Installation\n\n"
        "Run `./env/build.sh`.\n\n"
        "**Known Issue: tmol install fails on Python 3.12**\n\n"
        "```bash\n"
        'uv pip install "llvmlite>=0.41" "numba>=0.59"\n'
        "```\n"
    )

    def test_a_fix_documented_only_in_prose_is_reported(self, make_repo):
        root = make_repo(
            {"LICENSE": MIT, "README.md": self.KNOWN_ISSUE, "env/build.sh": "uv pip install tmol\n"}
        )
        item = finding(survey_repo(root), "env.fix-only-in-prose")
        assert "llvmlite" in item["evidence"][0]["quote"]

    def test_a_fix_inside_a_blockquote_callout_is_still_found(self, make_repo):
        """Known-issue notes are usually `>` callouts, fence and all.

        A fence detector that does not strip the quote marker never enters the
        block, so the check reports nothing in precisely the formatting where
        this pattern is most common — a silence that reads as a clean bill.
        """
        quoted = "\n".join("> " + line for line in self.KNOWN_ISSUE.splitlines())
        root = make_repo(
            {"LICENSE": MIT, "README.md": quoted, "env/build.sh": "uv pip install tmol\n"}
        )
        assert "env.fix-only-in-prose" in ids_of(survey_repo(root))

    def test_a_documented_fix_already_in_the_build_script_is_not_reported(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "README.md": self.KNOWN_ISSUE,
                "env/build.sh": 'uv pip install "llvmlite>=0.41" "numba>=0.59"\n'
                "uv pip install tmol\n",
            }
        )
        assert "env.fix-only-in-prose" not in ids_of(survey_repo(root))


class TestEnvironment:
    def test_torch_installed_only_by_a_shell_script_is_reported(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "pyproject.toml": '[project]\nname = "x"\ndependencies = ["numpy"]\n',
                "env/build.sh": "uv pip install torch==2.7.0+cu126 "
                "--index-url https://download.pytorch.org/whl/cu126\n",
            }
        )
        assert "env.torch-outside-manifest" in ids_of(survey_repo(root))

    def test_torch_declared_in_the_manifest_is_not_reported(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "pyproject.toml": '[project]\ndependencies = ["torch==2.7.0"]\n',
                "env/build.sh": "uv pip install torch==2.7.0\n",
            }
        )
        assert "env.torch-outside-manifest" not in ids_of(survey_repo(root))

    def test_the_python_constraint_becomes_a_checkable_requirement(self, make_repo):
        pyproject = '[project]\nrequires-python = ">=3.12"\n'
        root = make_repo({"LICENSE": MIT, "pyproject.toml": pyproject})
        assert finding(survey_repo(root), "env.python")["requires"] == {"python": ">=3.12"}

    def test_no_python_constraint_is_recorded_as_unknown(self, make_repo):
        root = make_repo({"LICENSE": MIT, "pyproject.toml": '[project]\nname = "x"\n'})
        assert "env.python" in inconclusive_checks(survey_repo(root))

    def test_a_stated_ubuntu_floor_becomes_an_os_and_glibc_requirement(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "README.md": "Requires Ubuntu 22.04+ or equivalent. Ubuntu 20.04 throws "
                "GLIBC errors.\n",
            }
        )
        requires = finding(survey_repo(root), "env.os-constraint")["requires"]
        assert requires["os"] == "linux"
        assert requires["glibc_min"] == "2.35"

    def test_cuda_is_read_from_the_wheel_tag(self, make_repo):
        root = make_repo(
            {"LICENSE": MIT, "env/build.sh": "uv pip install torch==2.7.0+cu126\n"}
        )
        assert finding(survey_repo(root), "hardware.cuda")["requires"] == {"cuda_min": "12.6"}


class TestWeightsAndData:
    def test_gated_hosts_are_grouped_by_provider_not_by_domain(self, make_repo):
        """NGC answers on two hostnames; two findings would read as two obstacles."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "env/download.sh": (
                    "wget https://api.ngc.nvidia.com/v2/models/a.ckpt\n"
                    "wget https://catalog.ngc.nvidia.com/models/b.ckpt\n"
                ),
            }
        )
        payload = survey_repo(root)
        ngc = [i for i in ids_of(payload) if i.startswith("weights.gated.nvidia-ngc")]
        assert len(ngc) == 1
        assert set(finding(payload, ngc[0])["requires"]["network_hosts"]) == {
            "api.ngc.nvidia.com",
            "ngc.nvidia.com",
        }

    def test_credentials_are_collected_as_a_requirement(self, make_repo):
        root = make_repo({"LICENSE": MIT, ".env_example": "HF_TOKEN=HF_TOKEN_HERE\nFOO=bar\n"})
        assert finding(survey_repo(root), "weights.credentials")["requires"] == {
            "env_vars": ["HF_TOKEN"]
        }

    def test_an_undocumented_download_size_is_unknown_not_zero(self, make_repo):
        root = make_repo({"LICENSE": MIT, "README.md": "Run the model.\n"})
        assert "weights.disk" in inconclusive_checks(survey_repo(root))

    def test_accession_lists_gate_training_and_leave_inference_alone(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "assets/data/pdb_multimer_ids.txt": "\n".join(f"1ab{n}" for n in range(200)),
            }
        )
        item = finding(survey_repo(root), "data.accession-lists")
        assert item["targets"] == ["training"]
        assert item["severity"] == "blocking"

    def test_a_short_id_file_is_not_mistaken_for_a_dataset(self, make_repo):
        root = make_repo({"LICENSE": MIT, "assets/data/target_ids.txt": "1abc\n2def\n"})
        assert "data.accession-lists" not in ids_of(survey_repo(root))

    def test_data_available_on_request_gates_training(self, make_repo):
        root = make_repo(
            {"LICENSE": MIT, "README.md": "Data are available from the authors upon request.\n"}
        )
        assert finding(survey_repo(root), "data.controlled-access")["targets"] == ["training"]


class TestHardware:
    def test_vram_is_gated_on_the_smallest_documented_figure(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "docs/hardware.md": "| GPU | Minimum VRAM 24 GB | Recommended VRAM 80 GB |\n",
                "run.py": "import torch\nassert torch.cuda.is_available()\n",
            }
        )
        assert finding(survey_repo(root), "hardware.vram")["requires"] == {"vram_gb": 24.0}

    def test_a_gpu_requirement_without_a_vram_figure_stays_unknown(self, make_repo):
        root = make_repo({"LICENSE": MIT, "run.py": "device = 'cuda'\nimport torch\n"})
        payload = survey_repo(root)
        assert "hardware.gpu" in ids_of(payload)
        assert "hardware.vram" in inconclusive_checks(payload)

    def test_named_datacentre_cards_are_reported_when_no_figure_is_given(self, make_repo):
        """"An A100" is a hint, not a number, and the report has to say which it has."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "README.md": "Trained on a node of A100 cards; H100 also works.\n",
                "run.py": "import torch\ndevice = 'cuda'\n",
            }
        )
        payload = survey_repo(root)
        found = finding(payload, "hardware.gpu-sku")
        assert "requires" not in found
        assert "hardware.vram" in inconclusive_checks(payload)

    def test_system_memory_on_the_same_line_is_not_read_as_vram(self, make_repo):
        """The figure has to be about the card, not merely near the word "memory"."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "docs/hardware.md": "Requires 24 GB VRAM, and 8 GB system memory for the loader.\n",
                "run.py": "import torch\nassert torch.cuda.is_available()\n",
            }
        )
        assert finding(survey_repo(root), "hardware.vram")["requires"] == {"vram_gb": 24.0}

    def test_a_document_that_only_sizes_system_ram_states_no_vram_figure(self, make_repo):
        """Reading host RAM as VRAM blocks hosts that would have run it — and passes
        hosts that will not, whenever the RAM figure happens to be the smaller one."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "docs/hardware.md": "The host needs 64 GB of memory and 500 GB of disk.\n",
                "run.py": "import torch\ndevice = 'cuda'\n",
            }
        )
        payload = survey_repo(root)
        assert "hardware.vram" not in ids_of(payload)
        assert "hardware.vram" in inconclusive_checks(payload)

    def test_a_gpu_figure_that_never_says_memory_is_still_read(self, make_repo):
        """Guard against narrowing the pattern into uselessness: this is how most
        READMEs actually word it."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "README.md": "Inference needs a GPU with at least 40 GB.\n",
                "run.py": "import torch\ndevice = 'cuda'\n",
            }
        )
        assert finding(survey_repo(root), "hardware.vram")["requires"] == {"vram_gb": 40.0}

    def test_cluster_training_is_deferred_to_the_training_target(self, make_repo):
        train = "#SBATCH --nodes=8\nsrun python t.py\n"
        root = make_repo({"LICENSE": MIT, "scripts/train.sh": train})
        assert finding(survey_repo(root), "hardware.cluster")["targets"] == ["training"]


class TestHandoff:
    def test_repo_shipped_agent_instructions_are_surfaced(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                ".claude/skills/setup/SKILL.md": "---\nname: setup\n---\n",
                "AGENTS.md": "# agents\n",
            }
        )
        item = finding(survey_repo(root), "handoff.repo-ships-guidance")
        assert {e["path"] for e in item["evidence"]} == {
            ".claude/skills/setup/SKILL.md",
            "AGENTS.md",
        }

    def test_vendored_tests_do_not_count_as_the_repository_having_tests(self, make_repo):
        """A borrowed package's test suite says nothing about this project."""
        root = make_repo(
            {
                "LICENSE": MIT,
                "community_models/colabdesign/af/test_utils.py": "def test_x(): pass\n",
            }
        )
        assert "handoff.no-tests" in ids_of(survey_repo(root))

    def test_a_repository_with_its_own_tests_is_not_flagged(self, make_repo):
        root = make_repo({"LICENSE": MIT, "tests/test_model.py": "def test_x(): pass\n"})
        assert "handoff.no-tests" not in ids_of(survey_repo(root))

    def test_ci_presence_is_noticed(self, make_repo):
        root = make_repo({"LICENSE": MIT, ".github/workflows/ci.yml": "on: push\n"})
        assert "handoff.no-ci" not in ids_of(survey_repo(root))


class TestPythonConstraint:
    def test_a_conda_pin_is_recorded_as_a_pep_440_specifier(self, make_repo):
        """Conda writes `python=3.10`. Passed through as-is it reached the gate as
        an unparseable clause, and one of those turns the entire report unknown —
        so an environment.yml was enough to stop the gate answering at all."""
        env = "name: demo\ndependencies:\n  - python=3.10\n  - pip\n"
        root = make_repo({"LICENSE": MIT, "environment.yml": env})
        assert finding(survey_repo(root), "env.python")["requires"] == {"python": "==3.10.*"}

    def test_an_ordinary_specifier_is_left_alone(self, make_repo):
        root = make_repo({"LICENSE": MIT, "pyproject.toml": 'requires-python = ">=3.12"\n'})
        assert finding(survey_repo(root), "env.python")["requires"] == {"python": ">=3.12"}


class TestProvenance:
    def test_a_directory_inside_someone_elses_checkout_claims_no_commit(self, make_repo):
        """`git -C dir` walks upwards. Stamping the report with the enclosing
        repository's SHA is worse than leaving it blank: the field exists so a
        reader can return to the exact tree that was read."""
        root = make_repo({"LICENSE": MIT, "README.md": "hello\n"})
        outer = root.parent
        subprocess.run(["git", "init", "-q", str(outer)], check=True)
        subprocess.run(["git", "-C", str(outer), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(outer), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "x"],
            check=True,
        )
        git = survey_repo(root)["repo"]["git"]
        assert git["commit"] is None
        assert "not the root of a git checkout" in git["why"]

    def test_a_real_checkout_root_reports_its_commit(self, make_repo):
        root = make_repo({"LICENSE": MIT, "README.md": "hello\n"})
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "x"],
            check=True,
        )
        assert len(survey_repo(root)["repo"]["git"]["commit"]) == 40


class TestPinning:
    """`env.unpinned` could not fire on a requirements.txt at all.

    The line filter skipped anything without an `=` or a `"` in it, which is
    the exact shape of the floating dependency the check exists to find.
    """

    def test_a_floating_requirements_file_is_reported(self, make_repo):
        deps = "\n".join(["numpy", "scipy", "torch", "pandas", "einops", "hydra-core"])
        pinned = "\n".join([f"pkg{n}==1.0" for n in range(4)])
        root = make_repo({"LICENSE": MIT, "requirements.txt": f"{deps}\n{pinned}\n"})
        found = finding(survey_repo(root), "env.unpinned")
        assert "6 of 10" in found["summary"]

    def test_a_lockfile_settles_the_question(self, make_repo):
        deps = "\n".join(f"dep{n}" for n in range(12))
        root = make_repo({"LICENSE": MIT, "requirements.txt": deps, "uv.lock": "version = 1\n"})
        assert "env.unpinned" not in ids_of(survey_repo(root))

    def test_project_metadata_is_not_counted_as_a_floating_dependency(self, make_repo):
        pyproject = (
            "[project]\n"
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'description = "a thing"\n'
            "dependencies = [\n"
            + "".join(f'  "dep{n}>=1.0",\n' for n in range(12))
            + "]\n"
        )
        root = make_repo({"LICENSE": MIT, "pyproject.toml": pyproject})
        payload = survey_repo(root)
        assert "env.unpinned" not in ids_of(payload)


class TestLicenceFilenames:
    @pytest.mark.parametrize("name", ["LICENSE", "LICENSE.md", "MIT-LICENSE.txt", "COPYING"])
    def test_a_licence_under_any_usual_name_is_found(self, make_repo, name):
        root = make_repo({name: MIT, "README.md": "hello\n"})
        assert "license.absent" not in ids_of(survey_repo(root))

    def test_a_licence_named_after_its_family_is_found(self, make_repo):
        root = make_repo({"Apache-2.0.txt": APACHE, "README.md": "hello\n"})
        assert "license.absent" not in ids_of(survey_repo(root))

    def test_an_unrelated_file_starting_with_a_family_name_is_not_a_licence(self, make_repo):
        """`mitigation.txt` must not read as MIT, or the check that says
        "no licence at all" stops meaning anything."""
        root = make_repo({"mitigation.txt": "how we mitigate\n", "README.md": "hello\n"})
        assert "license.absent" in ids_of(survey_repo(root))

    def test_a_pointer_licence_is_reported_once_and_does_not_leave_an_open_question(
        self, make_repo
    ):
        """It matches no signature because it holds no terms — which is what
        `license.root-is-a-pointer` already says. Recording it as inconclusive as
        well made every repository laid out this way report `unknown` overall,
        the worked example in this skill's own documentation included."""
        pointer = (
            "This repository contains components under different licenses.\n\n"
            "See the licenses/ directory for details.\n"
        )
        root = make_repo({"LICENSE": pointer, "licenses/code.txt": APACHE})
        payload = survey_repo(root)
        assert "license.root-is-a-pointer" in ids_of(payload)
        assert "license.identify" not in inconclusive_checks(payload)


class TestWhatTheSurveyCouldNotRead:
    """A file that was not read is not a file that said nothing.

    Every case here used to be indistinguishable from an empty file: the
    findings that file would have produced simply did not appear, and the
    report read as a clean one. This is the failure the whole skill exists to
    name, so it is checked from the outside — the finding must be gone *and*
    the reason must be on the record.
    """

    def _why(self, payload, check):
        return next(item["why"] for item in payload["inconclusive"] if item["check"] == check)

    def test_a_file_over_the_size_limit_is_recorded(self, make_repo, monkeypatch):
        """A 2.7 MB accession list took a blocking finding down with it."""
        monkeypatch.setattr(survey_module, "MAX_FILE_BYTES", 1_000)
        ids = "\n".join(f"{n:04d}_A" for n in range(400))
        root = make_repo({"LICENSE": MIT, "assets/data/pdb_multimer_ids.txt": ids})
        payload = survey_repo(root)
        assert "data.accession-lists" not in ids_of(payload)
        assert "pdb_multimer_ids.txt" in self._why(payload, "files.unread")
        assert payload["repo"]["files_unread"] == 1

    def test_an_unreadable_file_is_recorded_rather_than_read_as_empty(self, make_repo):
        root = make_repo({"LICENSE": MIT, "env/build.sh": "pip install atomworks || echo oops\n"})
        script = root / "env/build.sh"
        script.chmod(0o000)
        if os.access(script, os.R_OK):
            script.chmod(0o644)
            pytest.skip("this user can read a mode-000 file, so there is nothing to test")
        try:
            payload = survey_repo(root)
        finally:
            script.chmod(0o644)
        assert "env.swallowed-install-failure" not in ids_of(payload)
        assert "build.sh" in self._why(payload, "files.unread")

    def test_a_dangling_licence_symlink_is_not_reported_as_no_licence(self, make_repo):
        """`license.absent` is the strongest thing this script says. It may only be
        said about a repository that was looked at, not one that was skipped."""
        root = make_repo({"README.md": "hello\n"})
        (root / "LICENSE").symlink_to("terms/that/never/arrived.txt")
        payload = survey_repo(root)
        assert "license.absent" not in ids_of(payload)
        assert "LICENSE" in self._why(payload, "license.absent")

    def test_a_symlinked_licence_is_followed_and_read(self, make_repo):
        root = make_repo({"legal/terms.txt": APACHE, "README.md": "hello\n"})
        (root / "LICENSE").symlink_to("legal/terms.txt")
        payload = survey_repo(root)
        assert "license.absent" not in ids_of(payload)
        assert "license.absent" not in inconclusive_checks(payload)

    def test_index_truncation_is_recorded(self, make_repo, monkeypatch):
        monkeypatch.setattr(survey_module, "MAX_FILES", 5)
        root = make_repo({"LICENSE": MIT, **{f"src/mod{n}.py": "x = 1\n" for n in range(20)}})
        payload = survey_repo(root)
        assert payload["repo"]["files_indexed"] == 5
        assert payload["repo"]["files_over_index_limit"] == 16
        assert "files.index-truncated" in inconclusive_checks(payload)


class TestEvidenceDiscipline:
    def test_one_line_is_cited_once_however_many_groups_match(self, make_repo):
        root = make_repo(
            {
                "LICENSE": MIT,
                "docs/hw.md": "Minimum VRAM 24 GB, recommended VRAM 80 GB on one line.\n",
                "run.py": "import torch.cuda\n",
            }
        )
        item = finding(survey_repo(root), "hardware.vram")
        seen = [(e["path"], e["line"]) for e in item["evidence"]]
        assert len(seen) == len(set(seen))

    def test_every_finding_carries_a_source(self, make_repo):
        root = make_repo(
            {
                "LICENSE": "See the licenses/ directory for details.\n",
                "licenses/license_code.txt": APACHE,
                "licenses/license_weights.txt": RESEARCH_ONLY,
                "env/build.sh": "uv pip install foo || true\n",
                "pyproject.toml": '[project]\nrequires-python = ">=3.10"\n',
            }
        )
        for item in survey_repo(root)["findings"]:
            assert item["evidence"], f"{item['id']} has no evidence"
            assert all(e["path"] for e in item["evidence"])


class TestCommandLine:
    def test_a_url_is_refused_with_the_clone_command(self, capsys):
        code = survey_module.main(["https://github.com/owner/name"])
        assert code == 2
        assert "git clone --depth 1 https://github.com/owner/name" in capsys.readouterr().err

    def test_a_missing_directory_fails_loudly(self, capsys, tmp_path):
        assert survey_module.main([str(tmp_path / "nope")]) == 2
        assert "error:" in capsys.readouterr().err

    def test_json_output_round_trips(self, make_repo, capsys):
        root = make_repo({"LICENSE": MIT})
        assert survey_module.main([str(root)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == survey_module.SCHEMA
        assert payload["repo"]["name"] == root.name

    def test_text_output_names_the_evidence(self, make_repo, capsys):
        root = make_repo({"LICENSE": MIT, "env/build.sh": "pip install x || true\n"})
        survey_module.main([str(root), "--format", "text"])
        assert "env/build.sh:1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apache License\n   Version 2.0, January 2004", ["Apache-2.0"]),
        ("GNU AFFERO GENERAL PUBLIC LICENSE", ["AGPL-3.0"]),
        ("Attribution-NonCommercial 4.0", ["CC-BY-NC"]),
        ("Creative Commons Attribution 4.0", ["CC-BY-4.0"]),
        ("nothing recognisable", []),
    ],
)
def test_licence_signatures(text, expected):
    assert survey_module._identify(text) == expected
