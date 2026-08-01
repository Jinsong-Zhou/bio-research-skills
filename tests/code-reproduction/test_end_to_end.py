"""survey → probe → gate, over a repository built to trip several checks.

The unit tests each hold one piece still. This one runs the three scripts the
way a user does — one writing a file the next reads — because the seam between
them is where the schema, the requirement vocabulary and the verdict have to
agree, and none of those agreements is visible from inside a single module.
"""

from __future__ import annotations

import json

import gate as gate_module
import probe as probe_module
import pytest
from gate import assess, render_markdown
from survey import survey_repo

REPO = {
    "LICENSE": "This repository contains multiple components covered by different licenses.\n\n"
    "See the licenses/ directory for details.\n",
    "licenses/code.txt": "Apache License\nVersion 2.0, January 2004\n",
    "licenses/weights.txt": "Model Weights License\n\n"
    "The weights are released for non-commercial research purposes only.\n",
    "README.md": (
        "# Demo\n\n"
        "Inference needs a GPU with at least 40 GB VRAM, and 32 GB of system memory.\n"
        "Ubuntu 22.04+ is required; 20.04 throws GLIBC errors.\n"
        "Download the checkpoints from https://huggingface.co/demo/weights (about 60 GB).\n"
    ),
    ".env_example": "HF_TOKEN=\nWANDB_API_KEY=\n",
    "env/build_uv_env.sh": (
        "#!/usr/bin/env bash\n"
        "uv pip install torch==2.7.0+cu126 --index-url https://download.pytorch.org/whl/cu126\n"
        'uv pip install "atomworks[ml]" || echo "Warning: atomworks install failed"\n'
    ),
    "pyproject.toml": 'requires-python = ">=3.12"\ndependencies = ["lightning>=2.5.0,<2.6"]\n',
    "assets/data/pdb_multimer_ids.txt": "\n".join(f"{n:04d}_A" for n in range(300)),
    "run.py": "import torch\ndevice = 'cuda'\n",
}


@pytest.fixture
def survey_json(make_repo):
    return survey_repo(make_repo(REPO))


@pytest.fixture
def probe_json(monkeypatch, tmp_path):
    """A host with a small card, stubbed so no test touches real hardware."""
    monkeypatch.setattr(
        probe_module,
        "probe_gpu",
        lambda: {
            "available": True,
            "determined": True,
            "devices": [{"name": "RTX 4090", "vram_gb": 24.0, "driver": "550"}],
            "count": 1,
            "vram_gb": 24.0,
            "cuda": "12.6",
        },
    )
    monkeypatch.setattr(probe_module, "probe_platform", lambda: {
        "os": "linux", "release": "6.8", "machine": "x86_64", "glibc": "2.35",
        "libc": "glibc", "apple_silicon": False,
    })
    monkeypatch.setenv("HF_TOKEN", "not-a-real-token")
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    return probe_module.probe_host(disk_path=str(tmp_path), env_vars=["HF_TOKEN", "WANDB_API_KEY"])


class TestTheWholeRun:
    def test_the_probe_takes_its_questions_from_the_survey(self, survey_json, tmp_path):
        path = tmp_path / "survey.json"
        path.write_text(json.dumps(survey_json), encoding="utf-8")
        env_vars, hosts = probe_module.wanted_from_survey(str(path))
        assert "HF_TOKEN" in env_vars
        assert "huggingface.co" in hosts

    def test_a_card_below_the_stated_floor_blocks(self, survey_json, probe_json):
        report = assess(survey_json, probe_json)
        assert report["verdict"] == "blocked"
        vram = next(
            row for row in report["findings"] if row["id"] == "hardware.vram"
        )
        assert vram["gates"][0]["needed"] == 40.0
        assert vram["gates"][0]["found"] == 24.0

    def test_the_non_commercial_weights_licence_survives_to_the_report(
        self, survey_json, probe_json
    ):
        text = render_markdown(assess(survey_json, probe_json))
        assert "non-commercial" in text
        assert "licenses/weights.txt" in text

    def test_the_accession_lists_are_deferred_and_counted_out_loud(self, survey_json, probe_json):
        report = assess(survey_json, probe_json)
        assert "data.accession-lists" in {item["id"] for item in report["deferred"]}
        text = render_markdown(report)
        assert "Not evaluated" in text
        assert "--target training" in text

    def test_every_requirement_the_survey_states_is_one_the_gate_can_read(
        self, survey_json, probe_json
    ):
        """The two scripts share a file, not an import. This is the only thing
        keeping their vocabularies in step."""
        for raw in survey_json["findings"]:
            for key, value in (raw.get("requires") or {}).items():
                assert gate_module.evaluate(key, value, probe_json)["verdict"] in (
                    gate_module.VERDICTS
                )

    def test_the_markdown_report_names_a_file_for_every_finding_it_shows(
        self, survey_json, probe_json
    ):
        text = render_markdown(assess(survey_json, probe_json))
        for block in text.split("### ")[1:]:
            assert "Evidence: `" in block
