"""Every s3tx command against an in-process moto S3 and a local fake mediator: what
works today, pinned so later changes cannot lose it. Tests marked xfail(strict=True)
describe the intended behaviour where today's differs; a fix makes them pass, and
strict mode then fails until the marker is removed."""

import hashlib
import json
import re
import shutil
import socket
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import harness
import pytest


def _url(out):
    urls = [line.strip().strip('"') for line in out.splitlines() if "://" in line]
    urls = [u for u in urls if u.startswith(("http://", "https://"))]
    assert len(urls) == 1, out
    return urls[0]


def _code(out, verb="get"):
    m = re.search(rf"tx {verb} (?:<localfile> )?(\S+)$", out, re.MULTILINE)
    assert m, out
    return m.group(1)


def _registrations(server):
    return [json.loads(r["body"]) for r in server.requests if r["path"] == "/api/register"]


def _use_mediator(s3tx, server, monkeypatch):
    monkeypatch.setenv("S3TX_MEDIATOR", server.url)
    monkeypatch.setenv("S3TX_ADMIN_TOKEN", harness.ADMIN_TOKEN)


@pytest.fixture
def report(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 not really a pdf\n")
    return f


# --- send -------------------------------------------------------------------------


def test_send_uploads_and_prints_a_time_limited_link(cli, bucket, report):
    code, out, _ = cli("send", str(report))
    assert code == 0
    assert bucket.get_object(Bucket="s3tx", Key="report.pdf")["Body"].read() == report.read_bytes()
    url = _url(out)
    assert urlsplit(url).path.endswith("/report.pdf")
    assert abs(harness.url_lifetime(url) - 600) <= 5
    assert 'tx get "<that url>"' in out


def test_send_to_an_explicit_key(cli, bucket, report):
    assert cli("send", str(report), "in/box/r.pdf")[0] == 0
    assert bucket.head_object(Bucket="s3tx", Key="in/box/r.pdf")["ContentLength"] > 0


def test_send_reads_stdin_when_the_file_is_a_dash(cli, bucket):
    code, out, _ = cli("send", "-", "bundle.tgz", stdin=b"tarball bytes")
    assert code == 0
    assert bucket.get_object(Bucket="s3tx", Key="bundle.tgz")["Body"].read() == b"tarball bytes"
    assert "(stdin)" in out


def test_send_records_the_files_sha256_for_tx_to_check(cli, bucket, report):
    assert cli("send", str(report))[0] == 0
    meta = bucket.head_object(Bucket="s3tx", Key="report.pdf")["Metadata"]
    assert meta == {"sha256": hashlib.sha256(report.read_bytes()).hexdigest()}


def test_send_from_stdin_records_the_sha256_too(cli, bucket):
    assert cli("send", "-", "bundle.tgz", stdin=b"tarball bytes")[0] == 0
    meta = bucket.head_object(Bucket="s3tx", Key="bundle.tgz")["Metadata"]
    assert meta == {"sha256": hashlib.sha256(b"tarball bytes").hexdigest()}
    assert bucket.get_object(Bucket="s3tx", Key="bundle.tgz")["Body"].read() == b"tarball bytes"


def test_send_from_stdin_needs_a_key(cli, bucket):
    code, _, err = cli("send", "-", stdin=b"x")
    assert code == 1
    assert "reading from stdin needs an explicit key" in err


def test_send_a_missing_file_fails(cli, bucket, tmp_path):
    code, _, err = cli("send", str(tmp_path / "nope.bin"))
    assert code == 1
    assert "file not found" in err


def test_send_to_a_client_registers_a_single_use_code(
    s3tx, cli, bucket, server, report, monkeypatch
):
    _use_mediator(s3tx, server, monkeypatch)
    code, out, _ = cli("send", str(report), "--to", "laptop")
    assert code == 0
    (reg,) = _registrations(server)
    (req,) = [r for r in server.requests if r["path"] == "/api/register"]
    assert req["headers"]["Authorization"] == f"Bearer {harness.ADMIN_TOKEN}"
    assert reg["client"] == "laptop" and reg["method"] == "GET" and reg["ttl"] == 600
    assert len(reg["code"]) == 8 and set(reg["code"]) <= set(s3tx._CODE_ALPHABET)
    assert urlsplit(reg["url"]).path.endswith("/report.pdf")
    assert _code(out) == reg["code"]
    assert "single-use" in out


def test_send_to_retries_with_a_new_code_on_a_collision(
    s3tx, cli, bucket, server, report, monkeypatch
):
    _use_mediator(s3tx, server, monkeypatch)
    server.register_replies.append((409, {"error": "code exists"}))
    code, out, _ = cli("send", str(report), "--to", "laptop")
    assert code == 0
    first, second = _registrations(server)
    assert first["code"] != second["code"]
    assert _code(out) == second["code"] and second["code"] in server.codes


def test_send_to_fails_when_the_mediator_refuses(s3tx, cli, bucket, server, report, monkeypatch):
    _use_mediator(s3tx, server, monkeypatch)
    monkeypatch.setenv("S3TX_ADMIN_TOKEN", "wrong-token")
    code, _, err = cli("send", str(report), "--to", "laptop")
    assert code == 1
    assert "mediator register failed (HTTP 401)" in err


def test_send_to_needs_the_mediator_settings(cli, bucket, report):
    code, _, err = cli("send", str(report), "--to", "laptop")
    assert code == 1
    assert "S3TX_MEDIATOR and S3TX_ADMIN_TOKEN must be set to use --to" in err


def test_send_to_an_unreachable_mediator_fails(s3tx, cli, bucket, report, monkeypatch):
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("S3TX_MEDIATOR", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("S3TX_ADMIN_TOKEN", harness.ADMIN_TOKEN)
    code, _, err = cli("send", str(report), "--to", "laptop")
    assert code == 1
    assert "mediator unreachable" in err


def test_send_with_a_manifest_code(cli, bucket, report):
    code, out, _ = cli("send", str(report), "--code", "mycode1")
    assert code == 0
    assert _code(out) == "mycode1"
    body = bucket.get_object(Bucket="s3tx", Key=".s3tx-manifest")["Body"].read().decode()
    (line,) = body.splitlines()
    name, expires, url = line.split("\t")
    assert name == "mycode1" and urlsplit(url).path.endswith("/report.pdf")
    assert abs(int(expires) - (time.time() + 600)) <= 5


def test_send_with_an_auto_generated_manifest_code(s3tx, cli, bucket, report):
    code, out, _ = cli("send", str(report), "--code")
    assert code == 0
    generated = _code(out)
    assert len(generated) == 8 and set(generated) <= set(s3tx._CODE_ALPHABET)


def test_expires_is_capped_at_seven_days(cli, bucket, report):
    code, out, _ = cli("send", str(report), "--expires", "999999999")
    assert code == 0
    assert abs(harness.url_lifetime(_url(out)) - 604800) <= 5


def test_every_presigned_url_is_sigv4_on_the_bucket_host(s3tx, cli, bucket, report):
    # boto3 would sign with SigV2 by default. The host must not change either: the
    # mediator only accepts AWS hosts, or exactly ALLOWED_URL_HOST when it is pinned.
    urls = [
        _url(cli("send", str(report))[1]),
        _url(cli("drop", "incoming.zip")[1].split("tx put")[0]),
        _url(cli("manifest-url")[1].split("Seed it")[0]),
    ]
    for url in urls:
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"], url
        assert q["X-Amz-Credential"][0].endswith("/us-east-1/s3/aws4_request")
        assert "AWSAccessKeyId" not in q
        assert parts.netloc == f"{s3tx.BUCKET}.s3.amazonaws.com"


@pytest.mark.parametrize(
    "argv",
    [("send", "REPORT", "--to", "laptop"), ("drop", "incoming.zip", "--to", "laptop")],
    ids=["send", "drop"],
)
def test_to_reports_the_lifetime_the_mediator_granted(
    s3tx, cli, bucket, server, report, monkeypatch, argv
):
    # The mediator caps a code at 900 s whatever was asked; the code is what the far end
    # holds, so its lifetime is the one to print, with the clock time it dies.
    _use_mediator(s3tx, server, monkeypatch)
    argv = [str(report) if a == "REPORT" else a for a in argv]
    code, out, _ = cli(*argv, "--expires", "3600")
    assert code == 0
    assert "expires 900s" in out and "3600s" not in out
    assert re.search(r"Link expires in 900s \(~15 min, at \d\d:\d\d\)", out)


def test_a_mediator_that_does_not_say_falls_back_to_the_request(
    s3tx, cli, bucket, server, report, monkeypatch
):
    _use_mediator(s3tx, server, monkeypatch)
    server.register_replies.append((200, {"ok": True}))  # an older mediator: no ttl
    _, out, _ = cli("send", str(report), "--to", "laptop", "--expires", "300")
    assert "expires 300s" in out


# --- link -------------------------------------------------------------------------


def test_link_makes_a_fresh_link_for_an_existing_object(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="old.txt", Body=b"x")
    code, out, _ = cli("link", "old.txt")
    assert code == 0
    assert urlsplit(_url(out)).path.endswith("/old.txt")


def test_link_to_a_client(s3tx, cli, bucket, server, monkeypatch):
    _use_mediator(s3tx, server, monkeypatch)
    bucket.put_object(Bucket="s3tx", Key="old.txt", Body=b"x")
    code, out, _ = cli("link", "old.txt", "--to", "laptop")
    assert code == 0
    (reg,) = _registrations(server)
    assert reg["method"] == "GET" and _code(out) == reg["code"]


def test_link_with_a_manifest_code(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="old.txt", Body=b"x")
    code, out, _ = cli("link", "old.txt", "--code", "again12")
    assert code == 0 and _code(out) == "again12"


def test_link_a_missing_object_fails(cli, bucket):
    code, _, err = cli("link", "never-uploaded.txt")
    assert code == 1
    assert "object not found: s3://s3tx/never-uploaded.txt" in err


# --- drop and recv ----------------------------------------------------------------


def test_drop_prints_an_upload_link(cli, bucket):
    code, out, _ = cli("drop", "incoming.zip")
    assert code == 0
    url = _url(out.split("tx put")[0])
    assert urlsplit(url).path.endswith("/incoming.zip")
    assert 'tx put <localfile> "' in out and "curl -X PUT --upload-file" in out
    assert "s3tx recv incoming.zip" in out


def test_drop_to_a_client_registers_an_upload_code(s3tx, cli, bucket, server, monkeypatch):
    _use_mediator(s3tx, server, monkeypatch)
    code, out, _ = cli("drop", "incoming.zip", "--to", "laptop")
    assert code == 0
    (reg,) = _registrations(server)
    assert reg["method"] == "PUT" and urlsplit(reg["url"]).path.endswith("/incoming.zip")
    assert _code(out, "put") == reg["code"]
    assert "s3tx recv incoming.zip" in out


def test_recv_downloads_to_the_basename(cli, bucket, tmp_path, monkeypatch):
    bucket.put_object(Bucket="s3tx", Key="in/box/file.bin", Body=b"payload")
    monkeypatch.chdir(tmp_path)
    code, out, _ = cli("recv", "in/box/file.bin")
    assert code == 0
    assert (tmp_path / "file.bin").read_bytes() == b"payload"
    assert "Saved file.bin" in out


def test_recv_to_an_explicit_path(cli, bucket, tmp_path):
    bucket.put_object(Bucket="s3tx", Key="file.bin", Body=b"payload")
    target = tmp_path / "elsewhere.bin"
    assert cli("recv", "file.bin", str(target))[0] == 0
    assert target.read_bytes() == b"payload"


# --- ls, rm, rmrf -----------------------------------------------------------------


def test_ls_lists_every_object(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="a.txt", Body=b"12345")
    bucket.put_object(Bucket="s3tx", Key="dir/b.txt", Body=b"x" * 1234)
    code, out, _ = cli("ls")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 2
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d +5  a\.txt$", lines[0])
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d +1,234  dir/b\.txt$", lines[1])


def test_ls_with_a_prefix(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="a.txt", Body=b"1")
    bucket.put_object(Bucket="s3tx", Key="dir/b.txt", Body=b"2")
    _, out, _ = cli("ls", "dir/")
    assert out.strip().endswith("dir/b.txt") and "a.txt" not in out


def test_ls_of_an_empty_bucket(cli, bucket):
    assert cli("ls")[1].strip() == "(empty)"


def test_rm_deletes_one_object(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="a.txt", Body=b"1")
    bucket.put_object(Bucket="s3tx", Key="b.txt", Body=b"2")
    code, out, _ = cli("rm", "a.txt")
    assert code == 0 and "Deleted s3://s3tx/a.txt" in out
    keys = [o["Key"] for o in bucket.list_objects_v2(Bucket="s3tx")["Contents"]]
    assert keys == ["b.txt"]


def test_rm_a_missing_key_says_so_and_fails(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="keep.txt", Body=b"1")
    code, out, err = cli("rm", "never-there.txt")
    assert code == 1
    assert "object not found: s3://s3tx/never-there.txt" in err and "Deleted" not in out
    assert bucket.list_objects_v2(Bucket="s3tx")["KeyCount"] == 1


def test_a_refused_lookup_is_not_reported_as_a_missing_object(s3tx, cli, monkeypatch):
    from botocore.exceptions import ClientError

    class Refusing:
        def head_object(self, **_kw):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    monkeypatch.setattr(s3tx, "_preflight", lambda: (Refusing(), {}))
    for argv in (("rm", "x"), ("link", "x")):
        code, _, err = cli(*argv)
        assert code == 1
        assert "403" in err and "object not found" not in err


def test_rmrf_empties_the_bucket_after_confirmation(cli, bucket):
    for i in range(3):
        bucket.put_object(Bucket="s3tx", Key=f"k{i}", Body=b"x")
    code, out, err = cli("rmrf", answer="DELETE")
    assert code == 0 and "Emptied (3 objects)." in out
    assert "DELETE ALL objects in s3://s3tx" in err
    assert bucket.list_objects_v2(Bucket="s3tx")["KeyCount"] == 0


def test_rmrf_cancels_without_the_exact_word(cli, bucket):
    bucket.put_object(Bucket="s3tx", Key="keep", Body=b"x")
    code, _, err = cli("rmrf", answer="delete")
    assert code == 1 and "Cancelled." in err
    assert bucket.list_objects_v2(Bucket="s3tx")["KeyCount"] == 1


# --- status and lockdown ----------------------------------------------------------


def test_status_reports_a_private_bucket(cli, bucket):
    code, out, err = cli("status")
    assert code == 0
    assert "Profile  : (default credential chain)" in out
    assert "Identity : arn:aws:" in out
    assert "Bucket   : s3://s3tx    Region: us-east-1" in out
    assert "Public access: private (all four Block-Public-Access flags ON)" in out + err


def test_status_warns_when_the_bucket_is_partly_open(cli, bucket):
    bucket.put_public_access_block(
        Bucket="s3tx",
        PublicAccessBlockConfiguration={**harness.PRIVATE, "BlockPublicPolicy": False},
    )
    code, _, err = cli("status")
    assert code == 0
    assert "PARTIALLY OPEN" in err and "run: s3tx lockdown" in err


def test_status_warns_when_there_is_no_block_public_access_config(cli, bucket):
    bucket.delete_public_access_block(Bucket="s3tx")
    _, _, err = cli("status")
    assert "no Block-Public-Access config set" in err


def test_lockdown_turns_every_flag_on_and_drops_the_policy(cli, bucket):
    bucket.delete_public_access_block(Bucket="s3tx")
    bucket.put_bucket_policy(
        Bucket="s3tx",
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:DeleteBucket",
                        "Resource": "arn:aws:s3:::s3tx",
                    }
                ],
            }
        ),
    )
    code, out, _ = cli("lockdown")
    assert code == 0 and "Locked down" in out
    cfg = bucket.get_public_access_block(Bucket="s3tx")["PublicAccessBlockConfiguration"]
    assert cfg == harness.PRIVATE
    with pytest.raises(bucket.exceptions.ClientError):
        bucket.get_bucket_policy(Bucket="s3tx")


# --- regions --------------------------------------------------------------------


@pytest.fixture
def eu_bucket(bucket):
    """A second bucket, living in eu-west-1 while S3TX_REGION stays us-east-1."""
    import boto3

    boto3.client("s3", region_name="eu-west-1").create_bucket(
        Bucket="s3tx-eu", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
    )
    return "s3tx-eu"


def test_on_aws_links_are_signed_for_the_buckets_own_region(
    s3tx, cli, eu_bucket, report, monkeypatch
):
    monkeypatch.setenv("S3TX_BUCKET", eu_bucket)
    code, out, _ = cli("send", str(report))
    assert code == 0
    q = parse_qs(urlsplit(_url(out)).query)
    assert q["X-Amz-Credential"][0].endswith("/eu-west-1/s3/aws4_request")
    assert "Region: eu-west-1" in cli("status")[1]


@pytest.mark.parametrize(
    ("endpoint", "aws"),
    [
        ("https://s3.amazonaws.com", True),
        ("https://s3.us-west-2.amazonaws.com", True),
        ("https://s3.cn-north-1.amazonaws.com.cn", True),
        ("http://127.0.0.1:9000", False),
        ("https://storage.example.internal:9021", False),
        ("https://amazonaws.com.evil.example", False),
        ("https://evilamazonaws.com", False),
    ],
)
def test_what_counts_as_aws(s3tx, endpoint, aws):
    client = SimpleNamespace(meta=SimpleNamespace(endpoint_url=endpoint))
    assert s3tx._on_aws(client) is aws


# --- manifest-url and codes -------------------------------------------------------


def test_manifest_url_creates_the_manifest_and_prints_a_seed(cli, bucket):
    code, out, _ = cli("manifest-url", "--days", "30")
    assert code == 0
    assert "valid 7 day(s)" in out
    assert bucket.head_object(Bucket="s3tx", Key=".s3tx-manifest")["ContentLength"] == 0
    seed = _url(out.split("Seed it")[0])
    assert urlsplit(seed).path.endswith("/.s3tx-manifest")
    assert abs(harness.url_lifetime(seed) - 7 * 86400) <= 5
    assert 'export TX_MANIFEST="' in out and "~/.config/tx/manifest.url" in out


def test_codes_lists_only_live_manifest_codes(cli, bucket):
    now = int(time.time())
    body = f"live1\t{now + 3600}\thttps://x/1\ngone1\t{now - 10}\thttps://x/2\n"
    bucket.put_object(Bucket="s3tx", Key=".s3tx-manifest", Body=body.encode())
    code, out, _ = cli("codes")
    assert code == 0
    assert re.search(r"^live1 +expires in +(59|60) min$", out, re.MULTILINE)
    assert "gone1" not in out


def test_codes_with_none_live(cli, bucket):
    assert cli("codes")[1].strip() == "(no live codes)"


def test_a_new_manifest_code_prunes_expired_entries(cli, bucket, report):
    now = int(time.time())
    bucket.put_object(
        Bucket="s3tx", Key=".s3tx-manifest", Body=f"gone1\t{now - 10}\thttps://x\n".encode()
    )
    cli("send", str(report), "--code", "fresh12")
    body = bucket.get_object(Bucket="s3tx", Key=".s3tx-manifest")["Body"].read().decode()
    assert [line.split("\t")[0] for line in body.splitlines()] == ["fresh12"]


# --- preflight, help, errors ------------------------------------------------------


def test_a_missing_bucket_is_never_created(s3tx, cli, bucket, monkeypatch):
    monkeypatch.setenv("S3TX_BUCKET", "absent-bucket")
    code, _, err = cli("ls")
    assert code == 1
    assert "bucket s3://absent-bucket is not accessible in us-east-1" in err
    assert "s3tx never creates buckets" in err
    names = [b["Name"] for b in bucket.list_buckets()["Buckets"]]
    assert names == ["s3tx"]


def test_the_built_in_bucket_comes_with_a_warning(cli, bucket):
    code, _, err = cli("ls")
    assert code == 0
    assert "S3TX_BUCKET is not set, so s3tx uses its built-in default 's3tx'" in err
    assert "s3tx 2.0 drops the default" in err


def test_a_bucket_that_is_set_brings_no_warning(cli, bucket, monkeypatch):
    monkeypatch.setenv("S3TX_BUCKET", "s3tx")
    code, _, err = cli("ls")
    assert code == 0 and "S3TX_BUCKET" not in err


def test_an_unknown_profile_fails_clearly(s3tx, cli, bucket, monkeypatch):
    monkeypatch.setenv("S3TX_AWS_PROFILE", "nope")
    code, _, err = cli("ls")
    assert code == 1
    assert "AWS profile 'nope' not found" in err


@pytest.mark.parametrize("argv", [(), ("help",)])
def test_help_without_credentials(cli, argv):
    code, out, _ = cli(*argv)
    assert code == 0
    assert out.startswith("usage: s3tx")
    assert "How the far end receives it" in out


def test_an_unknown_command_is_a_usage_error(cli):
    code, _, err = cli("frobnicate")
    assert code == 2 and "invalid choice" in err


def test_version_needs_no_credentials(s3tx, cli):
    code, out, _ = cli("--version")
    assert code == 0
    assert out == f"s3tx {s3tx.__version__}\n"
    assert re.fullmatch(r"\d+\.\d+\.\d+", s3tx.__version__)


# --- doctor -----------------------------------------------------------------------


def _checks(out):
    """{text: state} for doctor's report lines ("ok    text", "FAIL  text", ...)."""
    return {line[6:]: line[:4].strip() for line in out.splitlines() if line[4:6] == "  "}


def _state(checks, start):
    (state,) = [s for text, s in checks.items() if text.startswith(start)]
    return state


@pytest.fixture
def repo_tx(monkeypatch):
    """This repository's tx as the one on PATH, so doctor finds the release's own."""
    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda n, *a, **k: str(harness.TX) if n == "tx" else real(n, *a, **k)
    )


def test_doctor_on_a_good_setup(s3tx, cli, bucket, server, monkeypatch, repo_tx):
    monkeypatch.setenv("S3TX_BUCKET", "s3tx")
    _use_mediator(s3tx, server, monkeypatch)
    code, out, err = cli("doctor")
    assert code == 0, out + err
    checks = _checks(out)
    assert "FAIL" not in checks.values(), out
    for start in ("bucket s3://s3tx in us-east-1", "public access: private", "credentials,"):
        assert _state(checks, start) == "ok", out
    assert _state(checks, f"mediator {server.url} answers") == "ok"
    assert _state(checks, "the mediator accepts S3TX_ADMIN_TOKEN") == "ok"
    assert _state(checks, "clock: within") == "ok"
    assert _state(checks, f"tx {s3tx.__version__} at ") == "ok"
    assert server.codes == {}  # the token check registers nothing


def test_doctor_fails_on_a_missing_bucket(cli, bucket, monkeypatch, repo_tx):
    monkeypatch.setenv("S3TX_BUCKET", "absent-bucket")
    code, out, err = cli("doctor")
    assert code == 1
    assert _state(_checks(out), "bucket s3://absent-bucket: not accessible") == "FAIL"
    assert "1 check(s) failed" in err


def test_doctor_reports_a_refused_admin_token(cli, bucket, server, monkeypatch, repo_tx):
    monkeypatch.setenv("S3TX_MEDIATOR", server.url)
    monkeypatch.setenv("S3TX_ADMIN_TOKEN", "not-the-token")
    code, out, _ = cli("doctor")
    assert code == 1
    assert _state(_checks(out), "the mediator refuses S3TX_ADMIN_TOKEN") == "FAIL"


def test_doctor_fails_on_an_unreachable_mediator(cli, bucket, monkeypatch, repo_tx):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    monkeypatch.setenv("S3TX_MEDIATOR", closed)
    code, out, _ = cli("doctor")
    assert code == 1
    assert _state(_checks(out), f"mediator {closed}: unreachable") == "FAIL"


def test_doctor_warns_about_the_built_in_bucket(cli, bucket, repo_tx):
    code, out, _ = cli("doctor")
    assert code == 0  # no mediator set: skipped, not failed
    assert _state(_checks(out), "S3TX_BUCKET is the built-in default 's3tx'") == "warn"


def test_doctor_warns_about_a_tx_too_old_to_say_its_version(cli, bucket, monkeypatch, tmp_path):
    old = tmp_path / "tx"
    old.write_text("#!/bin/sh\necho 'tx: unknown command: version' >&2\nexit 1\n")
    old.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: str(old) if n == "tx" else None)
    code, out, _ = cli("doctor")
    assert code == 0
    assert _state(_checks(out), f"tx at {old} is older than 1.5.0") == "warn"


def test_doctor_says_why_a_tx_that_fails_did_not_say_its_version(
    cli, bucket, monkeypatch, tmp_path
):
    broken = tmp_path / "tx"
    broken.write_text("#!/bin/sh\necho 'tx: curl is required but was not found' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: str(broken) if n == "tx" else None)
    code, out, _ = cli("doctor")
    assert code == 0
    start = f"tx at {broken} did not say its version: tx: curl is required but was not found"
    assert _state(_checks(out), start) == "warn"
