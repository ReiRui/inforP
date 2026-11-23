from __future__ import annotations

from typer.testing import CliRunner

from inforp.cli import app
from inforp.config import get_settings

RECIPIENTS = """version: 1
recipient_sets:
  test_only:
    name: 测试邮件组
    recipients:
      - email: me@example.com
        name: me
        active: true
  company_colleagues:
    name: 公司同事情报邮件组
    recipients:
      - email: alice@example.com
        active: true
      - email: bob@example.com
        active: false
"""


def test_list_recipients_shows_sets_and_members(tmp_path, monkeypatch):
    path = tmp_path / "recipients.yaml"
    path.write_text(RECIPIENTS, encoding="utf-8")
    monkeypatch.setenv("RECIPIENTS_FILE", str(path))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["list-recipients"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0
    assert "test_only" in result.output
    assert "company_colleagues" in result.output
    assert "me@example.com" in result.output
    assert "alice@example.com" in result.output
    assert "2/2" not in result.output  # company set has 1 active of 2 members
    assert "1/2 active" in result.output


def test_list_recipients_hides_inactive_when_asked(tmp_path, monkeypatch):
    path = tmp_path / "recipients.yaml"
    path.write_text(RECIPIENTS, encoding="utf-8")
    monkeypatch.setenv("RECIPIENTS_FILE", str(path))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["list-recipients", "--no-include-inactive"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0
    assert "alice@example.com" in result.output
    assert "bob@example.com" not in result.output


def test_list_recipients_explains_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RECIPIENTS_FILE", str(tmp_path / "missing.yaml"))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["list-recipients"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0
    assert "recipients.example.yaml" in result.output
