"""The zsh completion knows every command and option that the CLI contract pins, so the
two cannot drift apart; and zsh, where it is installed, reads the file."""

import json
import re
import shutil
import subprocess

import harness
import pytest

COMPLETION = harness.REPO / "completions" / "_s3tx"
CONTRACT = json.loads((harness.REPO / "tests" / "golden" / "cli-contract.json").read_text())


def test_every_command_is_completed():
    listed = set(re.findall(r"^\s+'([a-z-]+):", COMPLETION.read_text(), re.MULTILINE))
    assert listed == {c["name"] for c in CONTRACT["commands"]}


def test_every_option_is_completed():
    text = COMPLETION.read_text()
    flags = {
        flag
        for c in [CONTRACT, *CONTRACT["commands"]]
        for a in c.get("options", c.get("args", []))
        for flag in a["flags"]
        if flag.startswith("--") and flag != "--help"
    }
    for flag in sorted(flags):  # '--flag[...', '--flag=-[...' or '(- *)--flag[...'
        assert re.search(rf"'(\([^)]*\))?{re.escape(flag)}(=-)?\[", text), flag


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh is not installed")
def test_zsh_reads_it():
    r = subprocess.run(["zsh", "-n", str(COMPLETION)], capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr
