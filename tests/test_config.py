import json

from copilot.config import CONFIG_PATH, load_config


def test_default_config_is_created(tmp_path, monkeypatch):
    import copilot.config as config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.CONFIG_PATH.write_text(json.dumps({"model": "demo", "base_url": "http://localhost/v1", "api_key": "x"}))
    loaded = load_config()
    assert loaded["model"] == "demo"
    assert loaded["base_url"] == "http://localhost/v1"
