import pytest
from fbc import config


def test_env_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(config.NotConfigured):
        config.env("NOPE")


def test_env_present(monkeypatch):
    monkeypatch.setenv("FBC_X", "1")
    assert config.env("FBC_X") == "1"


def test_brand_tokens():
    assert config.BRAND == {
        "navy": "#16133F", "yellow": "#FFD23F", "pink": "#FF3E7F",
        "green": "#2EE59D", "white": "#FFFFFF",
    }
    assert config.SHORT_SLOTS_ET == ["07:00", "16:00"]
    assert config.LONG_SLOT_ET == ("SAT", "10:00")
