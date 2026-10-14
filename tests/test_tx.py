"""The far-end client, run as a real program under every POSIX shell available (CI's
sh is dash, a Mac's is bash), with a throwaway HOME and a local stand-in for the
mediator and for presigned URLs. Tests marked xfail(strict=True) describe intended
behaviour that differs from today's."""

import json
import os
import re
import shutil
import socket
import stat
import subprocess

import harness
import pytest

SHELLS = sorted({p for p in (shutil.which("sh"), shutil.which("dash")) if p})
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "config")
with open(os.path.join(FIXTURES, "expected.json")) as _f:
    EXPECTED = json.load(_f)


@pytest.fixture(params=SHELLS, ids=lambda p: os.path.basename(p))
def tx(request, tmp_path):
    home, work, tmp = tmp_path / "home", tmp_path / "work", tmp_path / "tmp"
    for d in (home, work, tmp):
        d.mkdir()

    def run(*args, env=None, stdin=None, umask=None):
        e = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp), "LC_ALL": "C"}
        e.update(env or {})
        cmd = [request.param, str(harness.TX), *args]
        if umask is not None:  # set inside the shell: preexec_fn is unsafe with threads
            cmd = [
                request.param,
                "-c",
                'umask "$1"; shift; exec "$0" "$@"',
                *cmd[:1],
                f"{umask:03o}",
                *cmd[1:],
            ]
        return subprocess.run(cmd, cwd=work, env=e, input=stdin, capture_output=True, timeout=60)

    run.home, run.work, run.tmp = home, work, tmp
    return run


def _mediator_env(server):
    return {"TX_MEDIATOR": server.url, "TX_CLIENT_ID": harness.CLIENT_ID}


def _config(tx, text):
    cfg = tx.home / ".config" / "tx"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config").write_text(text)
    (cfg / "config").chmod(0o600)  # as tx-enroll.sh leaves it


def _register(server, code, url, method="GET"):
    server.codes[code] = {"url": url, "client": harness.CLIENT, "method": method, "ttl": 600}


def _closed_port_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


# --- usage ------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("help",), ("-h",), ("--help",)])
def test_help(tx, args):
    r = tx(*args)
    assert r.returncode == 0
    assert b"tx get <url|code> [outfile]" in r.stdout and b"tx put <file|-> <url|code>" in r.stdout


def test_an_unknown_command(tx):
    r = tx("fetch")
    assert r.returncode == 1 and b"unknown command: fetch" in r.stderr


@pytest.mark.parametrize("args", [("get",), ("put",), ("put", "file-only")])
def test_missing_arguments(tx, args):
    r = tx(*args)
    assert r.returncode == 1 and b"usage: tx" in r.stderr


# --- get --------------------------------------------------------------------------


def test_get_a_url_saves_it_under_the_name_in_its_path(tx, server):
    server.files["report.txt"] = b"hello\n"
    r = tx("get", f"{server.url}/files/report.txt?X-Amz-Signature=abc&X-Amz-Expires=600")
    assert r.returncode == 0, r.stderr
    assert (tx.work / "report.txt").read_bytes() == b"hello\n"
    assert b"Saved report.txt" in r.stderr


def test_get_a_url_to_an_explicit_name(tx, server):
    server.files["report.txt"] = b"hello\n"
    assert tx("get", f"{server.url}/files/report.txt", "mine.txt").returncode == 0
    assert (tx.work / "mine.txt").read_bytes() == b"hello\n"


def test_a_failed_get_leaves_nothing_behind(tx, server):
    r = tx("get", f"{server.url}/files/missing.txt")
    assert r.returncode == 1 and b"download failed" in r.stderr
    assert list(tx.work.iterdir()) == [] and list(tx.tmp.iterdir()) == []


def test_get_a_code_through_the_mediator(tx, server):
    server.files["r.txt"] = b"via a code"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    r = tx("get", "Code2345", env=_mediator_env(server))
    assert r.returncode == 0, r.stderr
    assert (tx.work / "r.txt").read_bytes() == b"via a code"
    (req,) = [q for q in server.requests if q["path"] == "/api/get/Code2345"]
    assert req["headers"]["X-Client-Id"] == harness.CLIENT_ID


def test_a_code_works_once(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    assert tx("get", "Code2345", env=_mediator_env(server)).returncode == 0
    r = tx("get", "Code2345", "again.txt", env=_mediator_env(server))
    assert r.returncode == 1
    assert b"mediator refused: not found or expired (HTTP 404)" in r.stderr


def test_the_mediator_settings_come_from_the_config_file(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _config(tx, f"TX_MEDIATOR={server.url}\nTX_CLIENT_ID={harness.CLIENT_ID}\n")
    assert tx("get", "Code2345").returncode == 0


def test_a_config_saved_with_windows_line_endings_still_works(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _config(tx, f"TX_MEDIATOR={server.url}/\r\nTX_CLIENT_ID={harness.CLIENT_ID}\r\n")
    r = tx("get", "Code2345")
    assert r.returncode == 0, r.stderr


def test_the_environment_wins_over_the_config_file(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _config(tx, f"TX_MEDIATOR={_closed_port_url()}\nTX_CLIENT_ID=wrong\n")
    assert tx("get", "Code2345", env=_mediator_env(server)).returncode == 0


def test_a_mediator_without_a_client_id_is_an_error(tx, server):
    r = tx("get", "Code2345", env={"TX_MEDIATOR": server.url})
    assert r.returncode == 1
    assert b"TX_MEDIATOR is set but TX_CLIENT_ID is not" in r.stderr


def test_an_unknown_client_is_refused(tx, server):
    r = tx("get", "Code2345", env={"TX_MEDIATOR": server.url, "TX_CLIENT_ID": "stranger"})
    assert r.returncode == 1
    assert b"mediator refused: unknown client (HTTP 403)" in r.stderr


def test_an_unreachable_mediator(tx):
    r = tx("get", "Code2345", env={"TX_MEDIATOR": _closed_port_url(), "TX_CLIENT_ID": "x"})
    assert r.returncode == 1 and b"mediator unreachable" in r.stderr


def test_get_a_code_through_the_manifest(tx, server):
    server.files["m.txt"] = b"manifest path"
    server.manifest = (
        f"other12\t9999999999\thttps://nowhere\nMani2345\t9999999999\t{server.url}/files/m.txt\n"
    )
    r = tx("get", "Mani2345", env={"TX_MANIFEST": f"{server.url}/manifest"})
    assert r.returncode == 0, r.stderr
    assert (tx.work / "m.txt").read_bytes() == b"manifest path"


def test_the_manifest_seed_can_live_in_a_file(tx, server):
    server.files["m.txt"] = b"x"
    server.manifest = f"Mani2345\t9999999999\t{server.url}/files/m.txt\n"
    cfg = tx.home / ".config" / "tx"
    cfg.mkdir(parents=True)
    (cfg / "manifest.url").write_text(f"{server.url}/manifest")
    assert tx("get", "Mani2345").returncode == 0


def test_an_unknown_manifest_code(tx, server):
    server.manifest = "other12\t9999999999\thttps://nowhere\n"
    r = tx("get", "Mani2345", env={"TX_MANIFEST": f"{server.url}/manifest"})
    assert r.returncode == 1 and b"code 'Mani2345' not found" in r.stderr


def test_a_code_with_nothing_configured(tx):
    r = tx("get", "Code2345")
    assert r.returncode == 1
    assert b"is not a URL and neither TX_MEDIATOR nor TX_MANIFEST is set" in r.stderr


def test_one_setting_in_the_environment_and_the_other_in_the_config(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _config(tx, f"TX_CLIENT_ID={harness.CLIENT_ID}\n")
    r = tx("get", "Code2345", env={"TX_MEDIATOR": server.url})
    assert r.returncode == 0, r.stderr


@pytest.mark.xfail(
    strict=True,
    reason="with TX_MEDIATOR set, resolve() never falls back to the manifest, so a "
    "manifest code fails on a machine that also has a mediator",
)
def test_a_manifest_code_on_a_machine_with_a_mediator(tx, server):
    server.files["m.txt"] = b"x"
    server.manifest = f"Mani2345\t9999999999\t{server.url}/files/m.txt\n"
    env = {**_mediator_env(server), "TX_MANIFEST": f"{server.url}/manifest"}
    r = tx("get", "Mani2345", env=env)
    assert r.returncode == 0, r.stderr


@pytest.mark.xfail(
    strict=True,
    reason="tx get keeps mktemp's 0600 mode instead of the umask, unlike curl -o or wget",
)
def test_get_honours_the_umask(tx, server):
    server.files["run.sh"] = b"#!/bin/sh\necho hi\n"
    assert tx("get", f"{server.url}/files/run.sh", umask=0o022).returncode == 0
    assert stat.S_IMODE((tx.work / "run.sh").stat().st_mode) == 0o644


# --- put --------------------------------------------------------------------------


def test_put_a_file_to_a_url(tx, server):
    (tx.work / "up.bin").write_bytes(b"\x00\x01binary")
    r = tx("put", "up.bin", f"{server.url}/files/up.bin?X-Amz-Signature=abc")
    assert r.returncode == 0, r.stderr
    assert server.files["up.bin"] == b"\x00\x01binary"
    (req,) = [q for q in server.requests if q["method"] == "PUT"]
    assert req["headers"]["Content-Length"] == "8"
    assert b"Uploaded up.bin" in r.stderr


def test_put_stdin_sends_a_length_not_chunks(tx, server):
    r = tx("put", "-", f"{server.url}/files/piped.tgz", stdin=b"streamed bytes")
    assert r.returncode == 0, r.stderr
    assert server.files["piped.tgz"] == b"streamed bytes"
    (req,) = [q for q in server.requests if q["method"] == "PUT"]
    assert "Transfer-Encoding" not in req["headers"]
    assert b"Uploaded (stdin)" in r.stderr
    assert list(tx.tmp.iterdir()) == []  # the spool file is removed


def test_put_to_a_code_through_the_mediator(tx, server):
    (tx.work / "up.bin").write_bytes(b"to a code")
    _register(server, "Drop2345", f"{server.url}/files/inbox.bin", method="PUT")
    r = tx("put", "up.bin", "Drop2345", env=_mediator_env(server))
    assert r.returncode == 0, r.stderr
    assert server.files["inbox.bin"] == b"to a code"


def test_put_a_missing_file(tx, server):
    r = tx("put", "nope.bin", f"{server.url}/files/x")
    assert r.returncode == 1 and b"file not found: nope.bin" in r.stderr


def test_a_refused_put(tx, server):
    (tx.work / "up.bin").write_bytes(b"x")
    r = tx("put", "up.bin", f"{server.url}/elsewhere/up.bin")
    assert r.returncode == 1 and b"upload failed" in r.stderr


# --- the config file: the grammar shared with s3tx, and profiles -----------------------


def _shown(out):
    """{KEY: shown value} from `tx config`, where secrets appear as their length."""
    return dict(re.findall(r"^(TX_\w+)=(.*?)  \[", out.decode(), re.MULTILINE))


def _masked(value):
    return f"<set, {len(value)} characters>" if value else ""


@pytest.mark.parametrize(
    ("name", "section"),
    [(n, sec) for n, want in sorted(EXPECTED.items()) if "error_line" not in want for sec in want]
    + [(n, "default") for n, want in sorted(EXPECTED.items()) if "error_line" in want],
)
def test_the_grammar_matches_s3tx(tx, name, section):
    # The same fixture files s3tx's reader is held to; tx shows secrets by length only.
    want = EXPECTED[name]
    with open(os.path.join(FIXTURES, name), "rb") as f:
        _config(tx, f.read().decode("ascii"))
    r = tx("--profile", section, "config")
    if "error_line" in want:
        assert r.returncode == 1 and f"line {want['error_line']}:".encode() in r.stderr
        return
    assert r.returncode == 0, r.stderr
    vals, shown = want[section], _shown(r.stdout)
    assert shown["TX_MEDIATOR"] == vals.get("TX_MEDIATOR", "")
    assert shown["TX_CLIENT_ID"] == _masked(vals.get("TX_CLIENT_ID", ""))
    assert shown["TX_MANIFEST"] == _masked(vals.get("TX_MANIFEST", ""))


PROFILES = """\
TX_MEDIATOR={default_mediator}
TX_CLIENT_ID=wrong-client

[k8s]
TX_MEDIATOR={k8s_mediator}
TX_CLIENT_ID={client}
"""


def _profiles(tx, server):
    _config(
        tx,
        PROFILES.format(
            default_mediator=_closed_port_url(), k8s_mediator=server.url, client=harness.CLIENT_ID
        ),
    )


@pytest.mark.parametrize("flag", [("--profile", "k8s"), ("--profile=k8s",)])
def test_a_profile_picks_its_own_mediator(tx, server, flag):
    server.files["r.txt"] = b"through the k8s profile"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _profiles(tx, server)
    r = tx(*flag, "get", "Code2345")
    assert r.returncode == 0, r.stderr
    assert (tx.work / "r.txt").read_bytes() == b"through the k8s profile"


def test_a_named_profile_ignores_the_environment(tx, server):
    # An exported TX_MEDIATOR must not pull a named profile's code to another mediator.
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _profiles(tx, server)
    env = {"TX_MEDIATOR": _closed_port_url(), "TX_CLIENT_ID": "env-client"}
    r = tx("--profile", "k8s", "get", "Code2345", env=env)
    assert r.returncode == 0, r.stderr


def test_the_default_profile_lets_the_environment_win(tx, server):
    server.files["r.txt"] = b"x"
    _register(server, "Code2345", f"{server.url}/files/r.txt")
    _profiles(tx, server)  # its [default] points at a closed port with a wrong client
    r = tx("get", "Code2345", env=_mediator_env(server))
    assert r.returncode == 0, r.stderr


def test_config_shows_the_settings_and_their_sources_with_secrets_masked(tx, server):
    _profiles(tx, server)
    r = tx("config", env={"TX_MEDIATOR": "https://from-env.example"})
    out = r.stdout.decode()
    assert r.returncode == 0 and "Profile [default]" in out
    assert "TX_MEDIATOR=https://from-env.example  [env]" in out
    assert "TX_CLIENT_ID=<set, 12 characters>  [file]" in out and "wrong-client" not in out
    assert "TX_MANIFEST=  [unset]" in out
    k8s = tx("--profile", "k8s", "config").stdout.decode()
    assert f"TX_MEDIATOR={server.url}  [file]" in k8s and harness.CLIENT_ID not in k8s


def test_an_unknown_profile_is_an_error(tx, server):
    _profiles(tx, server)
    r = tx("--profile", "nope", "get", "Code2345")
    assert r.returncode == 1 and b"there is no profile [nope] in " in r.stderr


def test_a_named_profile_needs_the_file(tx):
    r = tx("--profile", "k8s", "get", "Code2345")
    assert r.returncode == 1 and b"does not exist" in r.stderr


@pytest.mark.parametrize("name", ["", "bad name", "../etc"])
def test_a_profile_name_is_checked(tx, name):
    r = tx("--profile", name, "config")
    assert r.returncode == 1 and b"a profile name is letters" in r.stderr


def test_a_config_others_can_read_gets_a_warning(tx, server):
    _profiles(tx, server)
    (tx.home / ".config" / "tx" / "config").chmod(0o644)
    r = tx("config")
    assert r.returncode == 0 and b"readable by other users" in r.stderr


def test_help_works_even_with_a_broken_config(tx):
    _config(tx, "this is not a setting\n")
    assert tx("help").returncode == 0
    r = tx("config")
    assert r.returncode == 1 and b"line 1:" in r.stderr


def test_a_named_profile_does_not_use_the_default_manifest_file(tx, server):
    server.files["m.txt"] = b"x"
    server.manifest = f"Mani2345\t9999999999\t{server.url}/files/m.txt\n"
    _config(tx, "[other]\nTX_MEDIATOR=\n")
    (tx.home / ".config" / "tx" / "manifest.url").write_text(f"{server.url}/manifest")
    assert tx("get", "Mani2345").returncode == 0  # [default] may use manifest.url
    r = tx("--profile", "other", "get", "Mani2345", "again.txt")
    assert r.returncode == 1 and b"neither TX_MEDIATOR nor TX_MANIFEST is set" in r.stderr
