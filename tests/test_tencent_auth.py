from voice_agent.processors.tencent_auth import (
    build_asr_ws_url,
    build_tts_ws_url,
    hmac_sha1_base64,
)


def test_hmac_sha1_base64_known_vector():
    assert hmac_sha1_base64("test-secret", "abc") == "LbbXHkgHYq7buziBOdqXH1z8g4w="
    assert hmac_sha1_base64(
        "test-secret",
        "asr.cloud.tencent.com/asr/v2/100?engine_model_type=16k_zh&expired=1700000000&needvad=1&nonce=1&secretid=sid&timestamp=1699990000&voice_format=1&voice_id=vid",
    ) == "KjYRe5KVRt3FngQ01yR86/adaD0="


def test_asr_url_uses_plain_host_without_get_prefix():
    url = build_asr_ws_url(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        voice_id="vid",
        timestamp=1699990000,
        expired=1700000000,
        nonce=1,
    )
    assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/100?")
    assert "signature=KjYRe5KVRt3FngQ01yR86%2FadaD0%3D" in url
    assert "engine_model_type=16k_zh" in url
    assert "voice_format=1" in url


def test_tts_url_uses_get_prefix_plaintext():
    url = build_tts_ws_url(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        session_id="sess",
        voice_type=101001,
        fast_voice_type="",
        speed=0,
        volume=0,
        timestamp=1700000000,
        expired=1700003600,
    )
    assert url.startswith("wss://tts.cloud.tencent.com/stream_wsv2?")
    assert "Action=TextToStreamAudioWSv2" in url
    assert "Signature=wQoNvBOqAnXRt5DECcm1%2BRZpTqQ%3D" in url
    assert "Codec=pcm" in url
    assert "SampleRate=16000" in url
    assert "FastVoiceType" not in url


def test_tts_fast_voice_forces_voice_type_200000000():
    url = build_tts_ws_url(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        session_id="sess",
        voice_type=101001,
        fast_voice_type="fast-1",
        speed=0,
        volume=0,
        timestamp=1700000000,
        expired=1700003600,
    )
    assert "VoiceType=200000000" in url
    assert "FastVoiceType=fast-1" in url
