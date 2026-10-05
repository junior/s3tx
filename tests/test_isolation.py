"""Guards for the test environment itself: if these fail, nothing else is trustworthy."""

import os
from pathlib import Path


def test_only_fake_credentials_and_no_user_config_reach_the_tests():
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert not Path(os.environ["AWS_CONFIG_FILE"]).exists()
    assert not Path(os.environ["AWS_SHARED_CREDENTIALS_FILE"]).exists()
    assert "AWS_PROFILE" not in os.environ
    assert not any(k.startswith("AWS_ENDPOINT_URL") for k in os.environ)
    assert [k for k in os.environ if k.startswith("S3TX_")] == ["S3TX_PROFILE"]
    assert os.environ["S3TX_PROFILE"] == ""
    assert not any(k.startswith("TX_") for k in os.environ)


def test_s3tx_resolves_the_fake_credentials(s3tx):
    assert s3tx.PROFILE == ""
    assert s3tx.MEDIATOR == "" and s3tx.ADMIN_TOKEN == ""
    assert s3tx._session().get_credentials().access_key == "testing"
