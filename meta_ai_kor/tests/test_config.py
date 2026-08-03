from app.config import Settings


def test_settings_reuse_existing_local_llm_defaults(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = Settings.from_env()

    assert settings.llm_base_url == "http://192.168.100.91:8000/v1"
    assert settings.llm_model == "Qwen3.6-27B-FP8"
    assert settings.llm_enable_thinking is False
    assert settings.auto_confirm_threshold == 85


def test_settings_accept_boolean_flags(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_ENABLE_THINKING", "true")
    monkeypatch.setenv("STRICT_LLM", "yes")

    settings = Settings.from_env()

    assert settings.llm_enabled is False
    assert settings.llm_enable_thinking is True
    assert settings.strict_llm is True
