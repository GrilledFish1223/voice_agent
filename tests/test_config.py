from voice_agent.config import SessionConfig, load_settings


def test_session_config_defaults():
    cfg = SessionConfig()
    assert cfg.voice_type == 101001
    assert cfg.music_mode == "client_url"
    assert cfg.vad_stop_secs == 0.5
    assert "语音助手" in cfg.system_prompt


def test_session_config_rejects_unknown_music_mode():
    try:
        SessionConfig(music_mode="stereo")
    except ValueError as exc:
        assert "music_mode" in str(exc)
    else:
        raise AssertionError("应拒绝非法 music_mode")


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("TENCENT_APP_ID", "100")
    monkeypatch.setenv("TENCENT_SECRET_ID", "sid")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    settings = load_settings()
    assert settings.tencent_app_id == "100"
    assert settings.llm_model == "deepseek-chat"
    assert settings.missing_required() == []
