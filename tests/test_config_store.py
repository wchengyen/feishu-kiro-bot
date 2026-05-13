import json
import os
import pytest
from dashboard.config_store import ConfigStore


@pytest.fixture
def old_config_file(tmp_path):
    path = tmp_path / "dashboard_config.json"
    path.write_text(json.dumps({"regions": ["cn-north-1"], "pins": ["ec2:cn-north-1:i-1"]}))
    return str(path)


@pytest.fixture
def new_config_file(tmp_path):
    path = tmp_path / "dashboard_config.json"
    path.write_text(json.dumps({
        "providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}},
        "pins": ["aws:ec2:cn-north-1:i-1"]
    }))
    return str(path)


def test_migrate_old_config(old_config_file, monkeypatch):
    monkeypatch.setattr("dashboard.config_store.CONFIG_PATH", old_config_file)
    store = ConfigStore()
    cfg = store.load()
    assert "providers" in cfg
    assert cfg["providers"]["aws"]["regions"] == ["cn-north-1"]
    assert cfg["pins"] == ["aws:ec2:cn-north-1:i-1"]


def test_read_new_config(new_config_file, monkeypatch):
    monkeypatch.setattr("dashboard.config_store.CONFIG_PATH", new_config_file)
    store = ConfigStore()
    cfg = store.load()
    assert cfg["providers"]["aws"]["enabled"] is True


def test_read_core_config_includes_model_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEFAULT_MODEL=deepseek-3.2\nBACKGROUND_MODEL=qwen3-coder-next\n"
    )
    store = ConfigStore(env_path=str(env_file))
    cfg = store.read_core_config()
    assert cfg["DEFAULT_MODEL"] == "deepseek-3.2"
    assert cfg["BACKGROUND_MODEL"] == "qwen3-coder-next"


def test_write_core_config_persists_model_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KIRO_AGENT=my-agent\n")
    store = ConfigStore(env_path=str(env_file))
    store.write_core_config({"DEFAULT_MODEL": "glm-5", "BACKGROUND_MODEL": ""})
    content = env_file.read_text()
    assert "DEFAULT_MODEL=glm-5" in content
    assert "BACKGROUND_MODEL=" in content
    assert "KIRO_AGENT=my-agent" in content
