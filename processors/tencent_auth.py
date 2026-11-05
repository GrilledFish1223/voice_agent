from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote, urlencode


def hmac_sha1_base64(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def _canonical_query(params: dict[str, object]) -> str:
    items = [(key, str(value)) for key, value in params.items() if value is not None and value != ""]
    items.sort(key=lambda item: item[0])
    return "&".join(f"{key}={value}" for key, value in items)


def _signed_query(plaintext: str, secret: str, params: dict[str, object], signature_key: str) -> str:
    signature = hmac_sha1_base64(secret, plaintext)
    signed = {key: value for key, value in params.items() if value is not None and value != ""}
    signed[signature_key] = signature
    return urlencode(signed, quote_via=quote, safe="")


def build_asr_ws_url(
    *,
    app_id: str,
    secret_id: str,
    secret_key: str,
    voice_id: str,
    timestamp: int,
    expired: int,
    nonce: int,
    engine_model_type: str = "16k_zh",
    vad_silence_time: int | None = None,
    filter_modal: int | None = None,
    filter_punc: int | None = None,
    convert_num_mode: int | None = None,
) -> str:
    params: dict[str, object] = {
        "engine_model_type": engine_model_type,
        "expired": expired,
        "needvad": 1,
        "nonce": nonce,
        "secretid": secret_id,
        "timestamp": timestamp,
        "voice_format": 1,
        "voice_id": voice_id,
    }
    if vad_silence_time is not None:
        params["vad_silence_time"] = int(vad_silence_time)
    if filter_modal is not None:
        params["filter_modal"] = int(filter_modal)
    if filter_punc is not None:
        params["filter_punc"] = int(filter_punc)
    if convert_num_mode is not None:
        params["convert_num_mode"] = int(convert_num_mode)
    plaintext = f"asr.cloud.tencent.com/asr/v2/{app_id}?{_canonical_query(params)}"
    query = _signed_query(plaintext, secret_key, params, "signature")
    return f"wss://asr.cloud.tencent.com/asr/v2/{app_id}?{query}"


def build_tts_ws_url(
    *,
    app_id: str,
    secret_id: str,
    secret_key: str,
    session_id: str,
    voice_type: int,
    fast_voice_type: str,
    speed: float,
    volume: float,
    timestamp: int,
    expired: int,
) -> str:
    resolved_voice = 200000000 if fast_voice_type else int(voice_type)
    params: dict[str, object] = {
        "Action": "TextToStreamAudioWSv2",
        "AppId": int(app_id),
        "Codec": "pcm",
        "Expired": expired,
        "FastVoiceType": fast_voice_type,
        "SampleRate": 16000,
        "SecretId": secret_id,
        "SessionId": session_id,
        "Speed": speed,
        "Timestamp": timestamp,
        "VoiceType": resolved_voice,
        "Volume": volume,
    }
    plaintext = f"GETtts.cloud.tencent.com/stream_wsv2?{_canonical_query(params)}"
    query = _signed_query(plaintext, secret_key, params, "Signature")
    return f"wss://tts.cloud.tencent.com/stream_wsv2?{query}"
