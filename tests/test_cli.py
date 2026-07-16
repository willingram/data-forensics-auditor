from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
import sys

import pytest

from data_forensics_auditor import __version__

CONSOLE_SCRIPTS = ("dfa", "data-forensics-auditor")


def run_cli(command: list[str], flag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command, flag],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("script_name", CONSOLE_SCRIPTS)
@pytest.mark.parametrize("flag", ("--help", "--version"))
def test_console_script_help_and_version(script_name: str, flag: str) -> None:
    script = shutil.which(script_name)
    assert script is not None, f"console script {script_name!r} is not installed"

    result = run_cli([script], flag)

    assert result.returncode == 0, result.stderr
    if flag == "--help":
        assert "usage: dfa" in result.stdout
    else:
        assert result.stdout.strip() == f"dfa {__version__}"


@pytest.mark.parametrize("flag", ("--help", "--version"))
def test_module_help_and_version(flag: str) -> None:
    result = run_cli([sys.executable, "-m", "data_forensics_auditor"], flag)

    assert result.returncode == 0, result.stderr
    if flag == "--help":
        assert "usage: dfa" in result.stdout
    else:
        assert result.stdout.strip() == f"dfa {__version__}"


def test_only_canonical_console_scripts_are_declared() -> None:
    distribution = importlib.metadata.distribution("data-forensics-auditor")
    scripts = {
        entry_point.name
        for entry_point in distribution.entry_points
        if entry_point.group == "console_scripts"
    }

    assert scripts == set(CONSOLE_SCRIPTS)
    assert scripts.isdisjoint({"audit", "data-forensics-audit"})
