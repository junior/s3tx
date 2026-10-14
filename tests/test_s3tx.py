"""Unit tests for s3tx: pure logic only, no AWS calls.

The s3tx CLI has no .py extension and a PEP 723 header, so we load it as a module
by path. Importing it does not pull in boto3 (that import is lazy), so these tests
run with nothing but the standard library + pytest.
"""

import importlib.machinery
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    loader = importlib.machinery.SourceFileLoader("s3tx_mod", str(REPO / "s3tx"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


s3tx = _load()


def test_gen_code_length_and_alphabet():
    code = s3tx._gen_code()
    assert len(code) == 8
    assert all(ch in s3tx._CODE_ALPHABET for ch in code)
    assert len(s3tx._gen_code(12)) == 12


def test_code_alphabet_excludes_ambiguous_chars():
    # No 0/O/1/l/I: codes are meant to be transcribed by a human.
    assert set(s3tx._CODE_ALPHABET).isdisjoint(set("01loIO"))


def test_clamp_expires():
    assert s3tx._clamp_expires(10) == 10
    assert s3tx._clamp_expires(10**12) == s3tx.MAX_EXPIRES
    assert s3tx._clamp_expires(0) == 1
    assert s3tx._clamp_expires(-5) == 1


def test_int_env(monkeypatch):
    assert s3tx._int_env("S3TX_NOPE_UNSET", 600) == 600
    monkeypatch.setenv("S3TX_TEST_EXPIRES", "42")
    assert s3tx._int_env("S3TX_TEST_EXPIRES", 600) == 42
    monkeypatch.setenv("S3TX_TEST_EXPIRES", "not-a-number")
    assert s3tx._int_env("S3TX_TEST_EXPIRES", 600) == 600  # falls back, no crash


class _Body:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw


class _FakeS3:
    def __init__(self, body=None, raises=False):
        self._body = body
        self._raises = raises

    def get_object(self, **_kw):
        if self._raises:
            raise RuntimeError("no such key")
        return {"Body": _Body(self._body)}


def test_manifest_read_parses_and_filters():
    body = b"abc\t1700000000\thttps://x\nbad\tnotnum\thttps://y\nonly\ttwo\n"
    out = s3tx._manifest_read(_FakeS3(body))
    assert out == [("abc", 1700000000, "https://x")]


def test_manifest_read_missing_returns_empty():
    assert s3tx._manifest_read(_FakeS3(raises=True)) == []
