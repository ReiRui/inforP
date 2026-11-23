from __future__ import annotations

from pathlib import Path

import pytest

from inforp.config import Settings, SourceConfig
from inforp.delivery import LocalFileDelivery, build_deliveries
from inforp.llm import DeepSeekResearchClient

PROFILE_YAML = """
version: 2
profile:
  focus_label: 示例行业
  focus_directions:
    - 方向甲
    - 方向乙
llm_search:
  enabled: true
  daily_queries: []
  weekly_queries: []
sources: []
"""


def _client_with(profile: dict) -> DeepSeekResearchClient:
    client = DeepSeekResearchClient.__new__(DeepSeekResearchClient)
    client.set_profile(profile)
    return client


def test_source_config_reads_profile(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(PROFILE_YAML, encoding="utf-8")

    config = SourceConfig(path)

    assert config.profile["focus_label"] == "示例行业"
    assert config.profile["focus_directions"] == ["方向甲", "方向乙"]


def test_source_config_rejects_bad_profile(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("version: 2\nprofile:\n  focus_directions: not-a-list\nsources: []\n", encoding="utf-8")

    with pytest.raises(ValueError):
        SourceConfig(path)


def test_focus_rule_uses_configured_profile():
    client = _client_with({"focus_label": "示例行业", "focus_directions": ["方向甲", "方向乙"]})

    rule = client._focus_rule()

    assert "示例行业" in rule
    assert "方向甲" in rule and "方向乙" in rule
    assert "verification threshold" in rule


def test_focus_rule_is_neutral_without_profile():
    client = _client_with(None)

    rule = client._focus_rule()

    assert "configured focus areas" in rule
    assert "industry" not in rule.lower()


def test_extraction_rules_do_not_hardcode_an_industry():
    client = _client_with({"focus_label": "示例行业", "focus_directions": ["方向甲"]})

    joined = " ".join(client._extraction_rules())

    assert "方向甲" in joined
    for hardcoded in ("EDA/IP industry", "user's company"):
        assert hardcoded not in joined


def test_local_file_channel_and_legacy_alias_both_write_files():
    settings = Settings(_env_file=None, DELIVERY_CHANNELS="local_file")

    deliveries = build_deliveries(settings)

    assert [type(d) for d in deliveries] == [LocalFileDelivery]
    assert deliveries[0].channel == "local_file"


def test_legacy_codex_file_channel_still_works():
    import os

    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
    settings = Settings(_env_file=None, DELIVERY_CHANNELS="codex_file")

    deliveries = build_deliveries(settings)

    assert [type(d) for d in deliveries] == [LocalFileDelivery]
