"""The s3tx command line as a contract: subcommands, their arguments, defaults and the
environment variables it reads. v1 keeps this stable, so any change shows up here.

The parser's structure is compared, not its help text, which varies between Python
versions. Regenerate after a deliberate change with:
    UPDATE_GOLDEN=1 python -m pytest tests/test_cli_contract.py
and review the diff of tests/golden/cli-contract.json like any other change.
"""

import argparse
import json
import os
from pathlib import Path

import harness
import pytest

GOLDEN = Path(__file__).parent / "golden" / "cli-contract.json"


class _Captured(Exception):
    pass


def _capture_parser(mod, monkeypatch):
    def fake_parse_args(self, *_args, **_kwargs):
        raise _Captured(self)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    try:
        mod.main()
    except _Captured as e:
        return e.args[0]
    raise AssertionError("s3tx main() never parsed its arguments")


def _args(actions):
    # The version string itself is left out, so a release does not rewrite the contract.
    return [
        {
            "flags": a.option_strings,
            "dest": a.dest,
            "nargs": a.nargs,
            "const": a.const,
            "default": a.default,
            "type": getattr(a.type, "__name__", None),
            "required": a.required,
            "metavar": a.metavar,
            "help": a.help,
        }
        for a in actions
        if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))
    ]


def _describe(parser):
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in sub._choices_actions}
    commands = [
        {"name": name, "help": helps.get(name), "args": _args(sp._actions)}
        for name, sp in sub.choices.items()
    ]
    return {
        "prog": parser.prog,
        "description": parser.description,
        "epilog": parser.epilog,
        "options": _args(parser._actions),
        "commands": commands,
    }


def test_cli_matches_the_recorded_contract(s3tx, monkeypatch):
    current = _describe(_capture_parser(s3tx, monkeypatch))
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2) + "\n")
    assert current == json.loads(GOLDEN.read_text())


@pytest.mark.parametrize(
    ("var", "attr", "value", "expected"),
    [
        ("S3TX_PROFILE", "PROFILE", "other", "other"),
        ("S3TX_BUCKET", "BUCKET", "my-bucket", "my-bucket"),
        ("S3TX_REGION", "REGION", "eu-west-1", "eu-west-1"),
        ("S3TX_EXPIRES", "EXPIRES", "120", 120),
        ("S3TX_MANIFEST_KEY", "MANIFEST_KEY", ".other-manifest", ".other-manifest"),
        ("S3TX_MEDIATOR", "MEDIATOR", "https://mediator.example/", "https://mediator.example"),
        ("S3TX_ADMIN_TOKEN", "ADMIN_TOKEN", "t0ken", "t0ken"),
    ],
)
def test_environment_variables_it_reads(monkeypatch, var, attr, value, expected):
    monkeypatch.setenv(var, value)
    assert getattr(harness.load_s3tx(), attr) == expected


def test_defaults_without_any_environment(monkeypatch):
    monkeypatch.delenv("S3TX_PROFILE")
    mod = harness.load_s3tx()
    assert (mod.PROFILE, mod.BUCKET, mod.REGION, mod.EXPIRES) == ("s3tx", "s3tx", "us-east-1", 600)
    assert (mod.MANIFEST_KEY, mod.MAX_EXPIRES) == (".s3tx-manifest", 604800)
