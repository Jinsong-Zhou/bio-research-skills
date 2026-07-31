"""The host probe: degrade, never raise, and never print a secret."""

from __future__ import annotations

import json
import shutil

import probe as probe_module
import pytest
from probe import (
    probe_disk,
    probe_env_vars,
    probe_gpu,
    probe_python,
    render_text,
    wanted_from_survey,
)

SMI_CSV = "NVIDIA H100 80GB HBM3, 81559, 550.90.07\nNVIDIA H100 80GB HBM3, 81559, 550.90.07"
SMI_BANNER = "| NVIDIA-SMI 550.90.07  Driver Version: 550.90.07  CUDA Version: 12.4 |"


class TestGpu:
    def test_no_nvidia_smi_reports_why_rather_than_just_false(self, monkeypatch):
        """"No GPU" and "no way to ask" are different claims.

        A host with a working card and no `nvidia-smi` on PATH is a
        configuration problem, not an unsuitable machine, and the report has
        to be able to say which one it saw.
        """
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = probe_gpu()
        assert result["available"] is False
        assert "nvidia-smi" in result["why"]
        assert result["vram_gb"] is None

    def test_a_present_but_silent_nvidia_smi_is_not_read_as_no_gpu(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(probe_module, "_run", lambda *a, **k: None)
        result = probe_gpu()
        assert result["available"] is False
        assert "driver or permission" in result["why"]

    def test_devices_are_parsed_and_the_smallest_card_is_the_figure(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            probe_module,
            "_run",
            lambda command, **k: SMI_CSV if "--query-gpu" in " ".join(command) else SMI_BANNER,
        )
        result = probe_gpu()
        assert result["count"] == 2
        assert result["vram_gb"] == 79.6
        assert result["cuda"] == "12.4"

    def test_an_unparseable_memory_column_does_not_abort_the_probe(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/nvidia-smi")
        def fake_run(command, **kwargs):
            query = "--query-gpu" in " ".join(command)
            return "Some Card, [N/A], 550.90" if query else ""

        monkeypatch.setattr(probe_module, "_run", fake_run)
        result = probe_gpu()
        assert result["available"] is True
        assert result["vram_gb"] is None


class TestCredentials:
    def test_only_presence_is_recorded_never_the_value(self, monkeypatch):
        """This file gets committed next to a reproduction log."""
        monkeypatch.setenv("HF_TOKEN", "hf_averysecretvalue")
        result = probe_env_vars(["HF_TOKEN"])
        assert result == {"HF_TOKEN": {"set": True}}
        assert "averysecret" not in json.dumps(result)

    def test_an_empty_variable_counts_as_unset(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "   ")
        assert probe_env_vars(["HF_TOKEN"])["HF_TOKEN"]["set"] is False

    def test_an_absent_variable_is_reported(self, monkeypatch):
        monkeypatch.delenv("NGC_API_KEY", raising=False)
        assert probe_env_vars(["NGC_API_KEY"])["NGC_API_KEY"]["set"] is False


class TestDisk:
    def test_a_path_that_does_not_exist_yet_walks_up_to_one_that_does(self, tmp_path):
        result = probe_disk(str(tmp_path / "weights" / "not" / "created"))
        assert result["free_gb"] is not None
        assert result["path"] == str(tmp_path)


class TestSurveyHandover:
    def test_credentials_and_hosts_come_from_the_survey(self, tmp_path):
        survey = {
            "findings": [
                {"id": "a", "requires": {"env_vars": ["HF_TOKEN"], "network_hosts": ["hf.co"]}},
                {"id": "b", "requires": {"vram_gb": 24}},
                {"id": "c"},
            ]
        }
        path = tmp_path / "survey.json"
        path.write_text(json.dumps(survey), encoding="utf-8")
        assert wanted_from_survey(str(path)) == (["HF_TOKEN"], ["hf.co"])

    def test_a_malformed_survey_stops_rather_than_probing_nothing(self, tmp_path):
        path = tmp_path / "survey.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="survey.json"):
            wanted_from_survey(str(path))


class TestOutput:
    def test_the_probe_records_which_interpreter_answered(self):
        """The Python running the probe is rarely the Python that runs the repo."""
        assert "need not be the one" in probe_python()["note"]

    def test_text_output_shouts_about_an_unset_credential(self):
        payload = {
            "platform": {"os": "linux", "release": "6.1", "machine": "x86_64", "glibc": "2.35"},
            "python": {"version": "3.12.1"},
            "gpu": {"available": False, "why": "no card"},
            "disk": {"free_gb": 10.0, "path": "/data"},
            "tools": {"git": {"present": True}},
            "env_vars": {"HF_TOKEN": {"set": False}},
            "reachability": {},
        }
        assert "HF_TOKEN: NOT SET" in render_text(payload)

    def test_no_hosts_means_no_connection_is_attempted(self):
        """The offline gate in conftest would fire if this reached the network."""
        payload = probe_module.probe_host(disk_path=".", env_vars=[], hosts=[])
        assert payload["reachability"] == {}
