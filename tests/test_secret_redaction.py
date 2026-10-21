import logging

from green.logging_setup import configure_logging
from green.security.secret_redaction import REDACTED, redact


def test_redacts_common_secrets():
    samples = {
        "key sk-proj-abcdefghijklmnopqrstuvwx": "sk-proj-abc",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123": "ghp_abc",
        "Authorization: Bearer abc.def.ghijklmnop": "abc.def.ghijklmnop",
        "password=hunter2": "hunter2",
        'api_key: "XYZ12345"': "XYZ12345",
        "AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
        "https://user:p4ss@example.com/x": "p4ss",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----": "MIIabc",
    }
    for text, secret in samples.items():
        out = redact(text)
        assert secret not in out, text
        assert REDACTED in out


def test_leaves_normal_text():
    text = "The token budget is 4096 and the password policy is documented."
    assert redact(text) == text


def test_log_file_is_redacted(tmp_path):
    configure_logging(tmp_path)
    logging.getLogger("green.test").warning("connecting with password=%s", "s3cr3tvalue")
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = (tmp_path / "green.log").read_text()
    assert "s3cr3tvalue" not in content
    assert REDACTED in content
