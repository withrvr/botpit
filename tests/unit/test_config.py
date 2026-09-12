from __future__ import annotations

import pytest

from botpit.config import Env, Settings, get_settings, reset_settings_cache


def test_defaults_are_dev(settings: Settings) -> None:
    assert settings.env is Env.DEV
    assert settings.event_bus == "memory"
    assert settings.llm_provider == "ollama_native"
    assert settings.is_sqlite
    assert not settings.is_postgres


def test_csv_lists_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODELS", "a:1, b:2 ,")
    monkeypatch.setenv("CORS_ORIGINS", '["http://x"]')
    s = Settings()
    assert s.llm_models == ["a:1", "b:2"]
    assert s.cors_origins == ["http://x"]


def test_env_selects_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOTPIT_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "real-secret")
    assert Settings().env is Env.PRODUCTION


def test_sample_rate_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SAMPLE_RATE", "1.5")
    with pytest.raises(ValueError, match="log_sample_rate"):
        Settings()


def test_get_settings_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_settings_cache()
    first = get_settings()
    monkeypatch.setenv("PORT", "9999")
    assert get_settings() is first
    reset_settings_cache()
    assert get_settings().port == 9999


def test_secret_not_in_repr(settings: Settings) -> None:
    assert "dev-only" not in repr(settings)


def test_env_file_resolved_per_instance(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    import pathlib

    root = pathlib.Path(str(tmp_path))
    (root / ".env.staging").write_text("PORT=4242\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("BOTPIT_ENV", "staging")
    assert Settings().port == 4242
    monkeypatch.setenv("BOTPIT_ENV", "dev")
    assert Settings().port == 8000


def test_production_rejects_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOTPIT_ENV", "production")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings()
    monkeypatch.setenv("JWT_SECRET", "real-secret")
    assert Settings().env is Env.PRODUCTION
