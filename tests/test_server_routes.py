import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from voice_agent.config import Settings
from voice_agent.server import create_app
from voice_agent.services import voice_catalog
from voice_agent.services.music_library import MusicLibrary
from voice_agent.services.voice_catalog import clone_voice

_REQUIRED_ENV = (
    "TENCENT_APP_ID",
    "TENCENT_SECRET_ID",
    "TENCENT_SECRET_KEY",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
)


def _settings(**overrides) -> Settings:
    data = {
        "tencent_app_id": "100",
        "tencent_secret_id": "sid",
        "tencent_secret_key": "secret",
        "llm_base_url": "https://example.invalid/v1",
        "llm_api_key": "sk-test",
        "llm_model": "demo",
        "host": "127.0.0.1",
        "port": 8765,
    }
    data.update(overrides)
    return Settings(**data)


def _app(tmp_path, settings: Settings | None = None):
    return create_app(
        settings=settings if settings is not None else _settings(),
        library=MusicLibrary(tmp_path),
    )


def _patch_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    class _BoundClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(voice_catalog.httpx, "AsyncClient", _BoundClient)


def test_health_reports_missing_keys_when_env_is_empty(monkeypatch, tmp_path):
    for name in _REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    app = create_app(library=MusicLibrary(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "missing": list(_REQUIRED_ENV)}


def test_music_lists_morning_breeze_after_seed(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/music")
    assert response.status_code == 200
    tracks = response.json()
    breeze = next(item for item in tracks if item["id"] == "morning_breeze")
    assert breeze["title"] == "晨光微风"
    assert breeze["url"] == "/api/music/stream/morning_breeze"
    assert (tmp_path / "morning_breeze.wav").is_file()


def test_music_stream_morning_breeze_is_wav(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/music/stream/morning_breeze")
        missing = client.get("/api/music/stream/not_a_track")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "audio/wav"
    assert response.content.startswith(b"RIFF")
    assert missing.status_code == 404


def test_voices_include_presets(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/voices")
    assert response.status_code == 200
    by_type = {item["voice_type"]: item for item in response.json()}
    assert by_type[101001] == {"voice_type": 101001, "name": "智瑜", "scene": "女声情感"}
    assert by_type[101004] == {"voice_type": 101004, "name": "智云", "scene": "男声通用"}
    assert by_type[101015] == {"voice_type": 101015, "name": "智萌", "scene": "女声可爱"}
    assert by_type[301031] == {"voice_type": 301031, "name": "爱小树", "scene": "超自然女声"}
    assert by_type[301032] == {"voice_type": 301032, "name": "爱小荷", "scene": "超自然男声"}
    assert by_type[501004] == {"voice_type": 501004, "name": "智小柔", "scene": "客服"}


def test_index_stays_available_without_call_page(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = Path(__file__).resolve().parents[1] / "static" / "index.html"
    if not page.is_file():
        assert "通话页还没有做好" in response.text
    else:
        assert "实时通话" in response.text
        assert "开始通话" in response.text


@pytest.mark.asyncio
async def test_clone_voice_sends_voice_clone_action():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["action"] = request.headers["X-TC-Action"]
        seen["version"] = request.headers["X-TC-Version"]
        seen["region"] = request.headers["X-TC-Region"]
        seen["authorization"] = request.headers["Authorization"]
        seen["host"] = request.url.host
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"Response": {"Data": {"FastVoiceType": "WCHN-real-1"}}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fast_voice_type = await clone_voice(
            _settings(),
            b"RIFFdemo",
            "你好",
            "我的声音",
            client=client,
        )
    assert fast_voice_type == "WCHN-real-1"
    assert seen["action"] == "VoiceClone"
    assert seen["version"] == "2020-08-24"
    assert seen["region"] == "ap-guangzhou"
    assert seen["host"] == "vrs.tencentcloudapi.com"
    authorization = str(seen["authorization"])
    assert authorization.startswith("TC3-HMAC-SHA256 ")
    assert "/vrs/tc3_request" in authorization
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["Codec"] == "wav"
    assert body["SampleRate"] == 16000
    assert body["Text"] == "你好"
    assert body["VoiceName"] == "我的声音"
    assert base64.b64decode(body["Audio"]) == b"RIFFdemo"


@pytest.mark.asyncio
async def test_clone_voice_error_becomes_runtime_error_without_fake_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-TC-Action"] == "VoiceClone"
        return httpx.Response(
            200,
            json={
                "Response": {
                    "Error": {"Code": "InvalidParameter", "Message": "音频不合格"},
                    "Data": {"FastVoiceType": "FAKE-VOICE-999"},
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError) as caught:
            await clone_voice(_settings(), b"RIFF", "你好", "我的声音", client=client)
    message = str(caught.value)
    assert "InvalidParameter" in message
    assert "音频不合格" in message
    assert "FAKE-VOICE-999" not in message


def test_clone_route_returns_tencent_error_message(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-TC-Action"] == "VoiceClone"
        return httpx.Response(
            200,
            json={
                "Response": {
                    "Error": {"Code": "InvalidParameter", "Message": "音频不合格"},
                    "Data": {"FastVoiceType": "FAKE-VOICE-999"},
                }
            },
        )

    _patch_client(monkeypatch, handler)
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/clone-voice",
            data={"text": "你好", "voice_name": "我的声音"},
            files={"audio": ("sample.wav", b"RIFFdemo", "audio/wav")},
        )
    assert response.status_code in (400, 502)
    detail = response.json()["detail"]
    assert "InvalidParameter" in detail
    assert "音频不合格" in detail
    assert "FAKE-VOICE-999" not in response.text
    assert "fast_voice_type" not in response.json()
