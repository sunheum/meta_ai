from app.config import Settings


def test_legacy_timeout_environment_sets_read_timeout(monkeypatch) -> None:
    monkeypatch.delenv("LLM_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "321")

    settings = Settings.from_env()

    assert settings.llm_read_timeout_seconds == 321
    assert settings.llm_connect_timeout_seconds == 15
