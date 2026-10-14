"""s3tx's configuration: the shared config-file grammar, profiles and their precedence,
and what an S3-compatible endpoint changes. tx reads the same grammar; its tests run the
same fixture files through tx."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "config"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())

CONFIG = """\
# the default profile needs no header
S3TX_BUCKET=home-bucket
S3TX_MEDIATOR=https://mediator.example.org
S3TX_ADMIN_TOKEN=file-token-123

[k8s]
S3TX_BUCKET=work-bucket
S3TX_ENDPOINT_URL=https://s3.internal.example:9021
S3TX_MEDIATOR=https://tx.internal.example
S3TX_ADMIN_TOKEN="k8s token"
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def write_config(home, text, mode=0o600):
    d = home / ".config" / "s3tx"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "config"
    f.write_text(text)
    f.chmod(mode)
    return f


# --- the grammar, on the fixtures tx is tested with too ---------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_grammar(s3tx, name):
    text = (FIXTURES / name).read_bytes().decode("ascii")
    want = EXPECTED[name]
    if "error_line" in want:
        with pytest.raises(s3tx._ConfigError) as e:
            s3tx._parse_config(text)
        assert e.value.line == want["error_line"]
    else:
        assert s3tx._parse_config(text) == want


# --- profiles ---------------------------------------------------------------------------


def test_the_default_profile_reads_the_file_and_the_environment_wins(cli, home, monkeypatch):
    write_config(home, CONFIG)
    monkeypatch.setenv("S3TX_MEDIATOR", "https://from-env.example")
    code, out, _ = cli("config")
    assert code == 0 and "Profile [default]" in out
    assert "S3TX_BUCKET=home-bucket  [file]" in out
    assert "S3TX_MEDIATOR=https://from-env.example  [env]" in out
    assert "S3TX_ADMIN_TOKEN=<set, 14 characters>  [file]" in out
    assert "S3TX_REGION=us-east-1  [built-in]" in out
    assert "file-token-123" not in out


def test_a_named_profile_stands_alone(cli, home, monkeypatch):
    # The environment's mediator and token must never reach a named profile: that is
    # how a code for an internal URL could end up at the wrong mediator.
    write_config(home, CONFIG)
    monkeypatch.setenv("S3TX_MEDIATOR", "https://from-env.example")
    monkeypatch.setenv("S3TX_ADMIN_TOKEN", "env-token")
    code, out, _ = cli("--profile", "k8s", "config")
    assert code == 0 and "Profile [k8s]" in out
    assert "S3TX_MEDIATOR=https://tx.internal.example  [file]" in out
    assert "S3TX_ADMIN_TOKEN=<set, 9 characters>  [file]" in out
    assert "S3TX_ENDPOINT_URL=https://s3.internal.example:9021  [file]" in out
    # Nothing comes from [default] either: what [k8s] leaves out is built in.
    assert "S3TX_AWS_PROFILE=s3tx  [built-in]" in out
    assert "S3TX_MANIFEST_KEY=.s3tx-manifest  [built-in]" in out


@pytest.mark.parametrize(
    "argv",
    [("--profile", "k8s", "config"), ("config", "--profile", "k8s"), ("--profile=k8s", "config")],
)
def test_the_profile_goes_before_or_after_the_command(cli, home, argv):
    write_config(home, CONFIG)
    code, out, _ = cli(*argv)
    assert code == 0 and "Profile [k8s]" in out


def test_an_unknown_profile_is_an_error(cli, home):
    path = write_config(home, CONFIG)
    code, _, err = cli("--profile", "nope", "config")
    assert code == 1 and f"there is no profile [nope] in {path}" in err


def test_a_named_profile_needs_the_file(cli, home):
    code, _, err = cli("--profile", "k8s", "ls")
    assert code == 1 and "does not exist" in err


def test_without_a_file_the_default_profile_is_the_environment(cli, home, monkeypatch):
    monkeypatch.setenv("S3TX_BUCKET", "env-bucket")
    code, out, _ = cli("config")
    assert code == 0
    assert "does not exist" in out and "S3TX_BUCKET=env-bucket  [env]" in out


def test_a_broken_file_names_the_line(cli, home):
    path = write_config(home, "S3TX_BUCKET=ok\nnot a setting\n")
    code, _, err = cli("config")
    assert code == 1 and f"{path}, line 2:" in err


def test_a_file_others_can_read_gets_a_warning(cli, home):
    write_config(home, CONFIG, mode=0o644)
    _, _, err = cli("config")
    assert "readable by other users" in err and "chmod 600" in err


def test_a_private_file_gets_no_warning(cli, home):
    write_config(home, CONFIG)
    assert "readable" not in cli("config")[2]


def test_the_aws_profile_setting_keeps_its_v1_name(cli, home, monkeypatch):
    write_config(
        home, "[old]\nS3TX_PROFILE=legacy\n[both]\nS3TX_PROFILE=legacy\nS3TX_AWS_PROFILE=new\n"
    )
    assert "S3TX_AWS_PROFILE=legacy  [file]" in cli("--profile", "old", "config")[1]
    assert "S3TX_AWS_PROFILE=new  [file]" in cli("--profile", "both", "config")[1]
    monkeypatch.setenv("S3TX_AWS_PROFILE", "from-env")  # beats the S3TX_PROFILE alias
    assert "S3TX_AWS_PROFILE=from-env  [env]" in cli("config")[1]


def test_a_profile_points_s3tx_at_its_own_bucket(cli, bucket, home):
    import boto3

    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="work-bucket")
    bucket.put_object(Bucket="s3tx", Key="home.txt", Body=b"1")
    bucket.put_object(Bucket="work-bucket", Key="work.txt", Body=b"2")
    write_config(home, "[work]\nS3TX_AWS_PROFILE=\nS3TX_BUCKET=work-bucket\n")
    code, out, _ = cli("--profile", "work", "ls")
    assert code == 0 and "work.txt" in out and "home.txt" not in out


# --- S3-compatible endpoints ------------------------------------------------------------


def test_an_s3_compatible_endpoint_gets_path_style_and_compatible_checksums(s3tx, monkeypatch):
    monkeypatch.setattr(s3tx, "ENDPOINT_URL", "https://s3.internal.example:9021")
    s3 = s3tx._s3()
    assert s3.meta.endpoint_url == "https://s3.internal.example:9021"
    assert not s3tx._on_aws(s3)
    assert s3.meta.config.s3["addressing_style"] == "path"
    assert s3.meta.config.request_checksum_calculation == "when_required"
    assert s3.meta.config.response_checksum_validation == "when_required"


def test_aws_keeps_its_own_defaults(s3tx):
    s3 = s3tx._s3()
    assert s3tx._on_aws(s3)
    assert (s3.meta.config.s3 or {}).get("addressing_style") != "path"
    assert s3.meta.config.request_checksum_calculation != "when_required"


def test_a_ca_bundle_verifies_the_endpoint(s3tx, monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("a stand-in, never opened\n")
    seen = []

    class Session:
        def client(self, service, **kw):
            seen.append(kw)
            return SimpleNamespace(meta=SimpleNamespace(endpoint_url=kw.get("endpoint_url")))

    monkeypatch.setattr(s3tx, "_session", Session)
    monkeypatch.setattr(s3tx, "ENDPOINT_URL", "https://s3.internal.example")
    monkeypatch.setattr(s3tx, "CA_BUNDLE", str(ca))
    s3tx._s3()
    assert seen and all(kw["verify"] == str(ca) for kw in seen)
