"""Shared fixtures. This file is imported before any test module, so it first strips
the caller's AWS, s3tx and tx settings and installs fake credentials: no test can
reach real AWS, a real mediator, or the user's own configuration."""

import atexit
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

import harness
import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="s3tx-tests-"))
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
(_SANDBOX / "home").mkdir()
for _key in list(os.environ):
    if _key.startswith(("AWS_", "S3TX_", "TX_")):
        del os.environ[_key]
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_CONFIG_FILE": str(_SANDBOX / "absent-aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(_SANDBOX / "absent-aws-credentials"),
        "AWS_EC2_METADATA_DISABLED": "true",
        "S3TX_PROFILE": "",  # the fake credentials above, never a named profile
        "HOME": str(_SANDBOX / "home"),  # never the user's ~/.config/s3tx or ~/.config/tx
        "NO_COLOR": "1",
    }
)


@pytest.fixture
def s3tx():
    """A fresh s3tx module for each test, so no state is carried between tests."""
    return harness.load_s3tx()


@pytest.fixture
def server():
    srv = harness.FakeServer()
    yield srv
    srv.close()


@pytest.fixture
def bucket():
    """An in-process moto S3 holding the s3tx bucket, private as in production."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="s3tx")
        s3.put_public_access_block(Bucket="s3tx", PublicAccessBlockConfiguration=harness.PRIVATE)
        yield s3


@pytest.fixture
def cli(s3tx, monkeypatch, capsys):
    """Run `s3tx ARGS...` in-process. Returns (exit code, stdout, stderr)."""

    def run(*argv, stdin=None, answer=None):
        monkeypatch.setattr(sys, "argv", ["s3tx", *argv])
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
        if answer is not None:
            monkeypatch.setattr("builtins.input", lambda _prompt="": answer)
        try:
            s3tx.main()
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else int(e.code is not None)
        out, err = capsys.readouterr()
        return code, out, err

    return run
