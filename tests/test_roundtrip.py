"""End to end, the way it is used: the real s3tx script on one side, the real tx script
and curl on the other, a moto S3 server on localhost in place of the bucket and the
local fake mediator in place of the real one. Bytes must arrive unchanged."""

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit

import boto3
import harness
import pytest

SH = shutil.which("dash") or shutil.which("sh")


@pytest.fixture(scope="module")
def s3_endpoint():
    from moto.server import ThreadedMotoServer

    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    srv.start()
    host, port = srv.get_host_and_port()
    yield f"http://{host}:{port}"
    srv.stop()


@pytest.fixture
def sides(s3_endpoint, server, tmp_path):
    """Run s3tx (the Mac) and tx (the far end) against the same local stand-ins."""
    s3 = boto3.client("s3", region_name="us-east-1", endpoint_url=s3_endpoint)
    s3.create_bucket(Bucket="s3tx")
    s3.put_public_access_block(Bucket="s3tx", PublicAccessBlockConfiguration=harness.PRIVATE)
    mac, far, home = tmp_path / "mac", tmp_path / "far", tmp_path / "home"
    for d in (mac, far, home):
        d.mkdir()

    def s3tx(*args, stdin=None, env=None):
        env = {
            **os.environ,
            "AWS_ENDPOINT_URL": s3_endpoint,
            "S3TX_MEDIATOR": server.url,
            "S3TX_ADMIN_TOKEN": harness.ADMIN_TOKEN,
            **(env or {}),
        }
        r = subprocess.run(
            [sys.executable, str(harness.S3TX), *args],
            cwd=mac,
            env=env,
            input=stdin,
            capture_output=True,
            timeout=120,
        )
        assert r.returncode == 0, r.stderr.decode()
        return r.stdout.decode()

    def tx(*args, stdin=None, env=None, check=True):
        e = {"PATH": os.environ["PATH"], "HOME": str(home), "LC_ALL": "C"}
        e.update(
            env
            if env is not None
            else {"TX_MEDIATOR": server.url, "TX_CLIENT_ID": harness.CLIENT_ID}
        )
        r = subprocess.run(
            [SH, str(harness.TX), *args],
            cwd=far,
            env=e,
            input=stdin,
            capture_output=True,
            timeout=120,
        )
        if check:
            assert r.returncode == 0, r.stderr.decode()
        return r

    yield s3tx, tx, mac, far
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket="s3tx"):
        for o in page.get("Contents", []):
            s3.delete_object(Bucket="s3tx", Key=o["Key"])
    s3.delete_bucket(Bucket="s3tx")


def _code(out, verb):
    return re.search(rf"tx {verb} (?:<localfile> )?(\S+)$", out, re.MULTILINE).group(1)


def test_a_file_out_to_the_far_end(sides):
    s3tx, tx, mac, far = sides
    (mac / "report.pdf").write_bytes(os.urandom(70_000))
    code = _code(s3tx("send", "report.pdf", "--to", "laptop"), "get")
    tx("get", code)
    assert (far / "report.pdf").read_bytes() == (mac / "report.pdf").read_bytes()
    again = tx("get", code, "second.pdf", check=False)
    assert again.returncode == 1 and b"(HTTP 404)" in again.stderr  # single use


def test_a_file_in_from_the_far_end(sides):
    s3tx, tx, mac, far = sides
    (far / "logs.txt").write_bytes(b"line\n" * 5000)
    code = _code(s3tx("drop", "inbox/logs.txt", "--to", "laptop"), "put")
    tx("put", "logs.txt", code)
    s3tx("recv", "inbox/logs.txt")
    assert (mac / "logs.txt").read_bytes() == (far / "logs.txt").read_bytes()


def _tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in (("a.txt", b"alpha\n"), ("dir/b.bin", os.urandom(4096))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_many_files_out_through_a_pipe(sides):
    s3tx, tx, _mac, far = sides
    bundle = _tarball()
    code = _code(s3tx("send", "-", "bundle.tgz", "--to", "laptop", stdin=bundle), "get")
    tx("get", code)
    assert (far / "bundle.tgz").read_bytes() == bundle
    with tarfile.open(far / "bundle.tgz") as t:
        assert t.getnames() == ["a.txt", "dir/b.bin"]


def test_many_files_in_through_a_pipe(sides):
    s3tx, tx, mac, _far = sides
    bundle = _tarball()
    code = _code(s3tx("drop", "bundle.tgz", "--to", "laptop"), "put")
    tx("put", "-", code, stdin=bundle)
    s3tx("recv", "bundle.tgz")
    assert (mac / "bundle.tgz").read_bytes() == bundle


def test_the_manifest_path_needs_no_mediator(sides):
    s3tx, tx, mac, far = sides
    (mac / "notes.md").write_bytes(b"# notes\n")
    s3tx("send", "notes.md", "--code", "Mani2345")
    seed = next(
        line.strip() for line in s3tx("manifest-url").splitlines() if line.startswith("http")
    )
    tx("get", "Mani2345", env={"TX_MANIFEST": seed})
    assert (far / "notes.md").read_bytes() == b"# notes\n"


def test_a_plain_link_needs_neither(sides):
    s3tx, tx, mac, far = sides
    (mac / "plain.bin").write_bytes(b"no code at all")
    url = next(line for line in s3tx("send", "plain.bin").splitlines() if line.startswith("http"))
    tx("get", url, env={})
    assert (far / "plain.bin").read_bytes() == b"no code at all"


def test_a_custom_endpoint_keeps_the_configured_region(s3_endpoint, sides):
    # Only AWS gets the bucket's region detected. Against any other endpoint, here the
    # local moto server, s3tx signs for S3TX_REGION as configured, even though this
    # endpoint, like AWS, reports where the bucket lives.
    s3tx, _tx, mac, _far = sides
    other = boto3.client("s3", region_name="eu-west-1", endpoint_url=s3_endpoint)
    other.create_bucket(
        Bucket="s3tx-eu", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
    )
    try:
        probe = boto3.client("s3", region_name="us-east-1", endpoint_url=s3_endpoint)
        head = probe.head_bucket(Bucket="s3tx-eu")
        assert head["ResponseMetadata"]["HTTPHeaders"]["x-amz-bucket-region"] == "eu-west-1"
        (mac / "f.txt").write_bytes(b"x")
        out = s3tx("send", "f.txt", env={"S3TX_BUCKET": "s3tx-eu"})
        url = next(line for line in out.splitlines() if line.startswith("http"))
        assert "%2Fus-east-1%2Fs3%2Faws4_request" in url
    finally:
        for o in other.list_objects_v2(Bucket="s3tx-eu").get("Contents", []):
            other.delete_object(Bucket="s3tx-eu", Key=o["Key"])
        other.delete_bucket(Bucket="s3tx-eu")


def test_a_generic_s3_profile_end_to_end(s3_endpoint, server, tmp_path):
    # A named profile for an S3-compatible server with its own mediator, used from both
    # ends with --profile, while the environment points at a mediator that is not there:
    # if a named profile ever took a setting from the environment, this would fail.
    closed = "http://127.0.0.1:9"  # nothing listens on the discard port
    s3 = boto3.client("s3", region_name="us-east-1", endpoint_url=s3_endpoint)
    s3.create_bucket(Bucket="generic-bucket")
    mac_home, far_home, mac, far = (tmp_path / d for d in ("mac-home", "far-home", "mac", "far"))
    for d in (mac / ".", far / ".", mac_home / ".config/s3tx", far_home / ".config/tx"):
        d.mkdir(parents=True)
    cfg = mac_home / ".config/s3tx/config"
    cfg.write_text(
        "S3TX_BUCKET=not-this-one\n"
        "[generic]\n"
        "S3TX_AWS_PROFILE=\n"
        "S3TX_BUCKET=generic-bucket\n"
        f"S3TX_ENDPOINT_URL={s3_endpoint}\n"
        f"S3TX_MEDIATOR={server.url}\n"
        f"S3TX_ADMIN_TOKEN={harness.ADMIN_TOKEN}\n"
    )
    cfg.chmod(0o600)
    tcfg = far_home / ".config/tx/config"
    tcfg.write_text(
        f"TX_MEDIATOR={closed}\nTX_CLIENT_ID=wrong\n"
        f"[k8s]\nTX_MEDIATOR={server.url}\nTX_CLIENT_ID={harness.CLIENT_ID}\n"
    )
    tcfg.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_ENDPOINT_URL")}
    env.update(HOME=str(mac_home), S3TX_MEDIATOR=closed, S3TX_ADMIN_TOKEN="env-token")

    def s3tx(*args, check=True):
        r = subprocess.run(
            [sys.executable, str(harness.S3TX), *args],
            cwd=mac,
            env=env,
            capture_output=True,
            timeout=120,
        )
        if check:
            assert r.returncode == 0, r.stderr.decode()
        return r

    try:
        (mac / "f.bin").write_bytes(os.urandom(5000))
        out = s3tx("--profile", "generic", "send", "f.bin", "--to", "laptop").stdout.decode()
        code = _code(out, "get")
        (entry,) = server.codes.values()
        assert urlsplit(entry["url"]).path.startswith("/generic-bucket/")  # path-style
        far_env = {"PATH": os.environ["PATH"], "HOME": str(far_home), "LC_ALL": "C"}
        far_env["TX_MEDIATOR"] = closed
        r = subprocess.run(
            [SH, str(harness.TX), "--profile", "k8s", "get", code],
            cwd=far,
            env=far_env,
            capture_output=True,
            timeout=120,
        )
        assert r.returncode == 0, r.stderr.decode()
        assert (far / "f.bin").read_bytes() == (mac / "f.bin").read_bytes()

        status = s3tx("--profile", "generic", "status").stdout.decode()
        assert "Config   : profile [generic]" in status
        assert "Identity : not checked" in status and "Public access: not checked" in status
        lock = s3tx("--profile", "generic", "lockdown", check=False)
        assert lock.returncode == 1 and b"not AWS" in lock.stderr
    finally:
        for o in s3.list_objects_v2(Bucket="generic-bucket").get("Contents", []):
            s3.delete_object(Bucket="generic-bucket", Key=o["Key"])
        s3.delete_bucket(Bucket="generic-bucket")
