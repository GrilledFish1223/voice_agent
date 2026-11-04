from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx

from voice_agent.config import Settings

_HOST = "vrs.tencentcloudapi.com"
_SERVICE = "vrs"
_ENDPOINT = f"https://{_HOST}/"
_ACTION = "VoiceClone"
_VERSION = "2020-08-24"
_REGION = "ap-guangzhou"
_CONTENT_TYPE = "application/json; charset=utf-8"
_SIGNED_HEADERS = "content-type;host;x-tc-action"
_ALGORITHM = "TC3-HMAC-SHA256"

PRESET_VOICES: tuple[dict[str, object], ...] = (
    {"voice_type": 101001, "name": "智瑜", "scene": "女声情感"},
    {"voice_type": 101004, "name": "智云", "scene": "男声通用"},
    {"voice_type": 101015, "name": "智萌", "scene": "女声可爱"},
    {"voice_type": 301031, "name": "爱小树", "scene": "超自然女声"},
    {"voice_type": 301032, "name": "爱小荷", "scene": "超自然男声"},
    {"voice_type": 501004, "name": "智小柔", "scene": "客服"},
)


def list_voices() -> list[dict[str, object]]:
    return [dict(item) for item in PRESET_VOICES]


async def clone_voice(
    settings: Settings,
    audio: bytes,
    text: str,
    voice_name: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """向腾讯云申请一句话复刻。成功时只返回响应里的 FastVoiceType。"""
    payload = _request_body(audio, text, voice_name)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    headers = {
        "Authorization": _authorization(settings, payload, timestamp),
        "Content-Type": _CONTENT_TYPE,
        "Host": _HOST,
        "X-TC-Action": _ACTION,
        "X-TC-Version": _VERSION,
        "X-TC-Region": _REGION,
        "X-TC-Timestamp": str(timestamp),
    }
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        try:
            response = await client.post(_ENDPOINT, content=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"腾讯云请求失败: {exc}") from exc
        return _fast_voice_type(response)
    finally:
        if owns_client:
            await client.aclose()


def _request_body(audio: bytes, text: str, voice_name: str) -> bytes:
    body = {
        "SessionId": str(uuid.uuid4()),
        "VoiceName": voice_name,
        "Text": text,
        "Audio": base64.b64encode(audio).decode("ascii"),
        "Codec": "wav",
        "SampleRate": 16000,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _authorization(settings: Settings, payload: bytes, timestamp: int) -> str:
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    canonical_headers = (
        f"content-type:{_CONTENT_TYPE}\n"
        f"host:{_HOST}\n"
        f"x-tc-action:{_ACTION.lower()}\n"
    )
    hashed_payload = hashlib.sha256(payload).hexdigest()
    canonical_request = (
        "POST\n"
        "/\n"
        "\n"
        f"{canonical_headers}\n"
        f"{_SIGNED_HEADERS}\n"
        f"{hashed_payload}"
    )
    scope = f"{date}/{_SERVICE}/tc3_request"
    string_to_sign = "\n".join(
        (
            _ALGORITHM,
            str(timestamp),
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        )
    )
    secret_date = _hmac(("TC3" + settings.tencent_secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, _SERVICE)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"{_ALGORITHM} "
        f"Credential={settings.tencent_secret_id}/{scope}, "
        f"SignedHeaders={_SIGNED_HEADERS}, "
        f"Signature={signature}"
    )


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _fast_voice_type(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"腾讯云返回了无法解析的响应: {response.status_code}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("腾讯云返回了无法解析的响应")
    body = payload.get("Response")
    if not isinstance(body, dict):
        raise RuntimeError("腾讯云返回了无法解析的响应")
    error = body.get("Error")
    if error:
        if isinstance(error, dict):
            code = str(error.get("Code", ""))
            message = str(error.get("Message", ""))
            raise RuntimeError(f"{code}: {message}")
        raise RuntimeError(str(error))
    data = body.get("Data")
    fast_voice_type = data.get("FastVoiceType") if isinstance(data, dict) else None
    if not isinstance(fast_voice_type, str) or not fast_voice_type:
        raise RuntimeError("腾讯云没有返回 FastVoiceType")
    return fast_voice_type
