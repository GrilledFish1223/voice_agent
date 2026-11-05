from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone

from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language

from voice_agent.processors.socket_errors import is_abrupt_close
from voice_agent.processors.tencent_auth import build_asr_ws_url
from voice_agent.processors.ws_serializer import client_message

Connect = Callable[[str], Awaitable[object]]

# 腾讯云实时 ASR：1 秒不超过 25 片，官方推荐 200ms / 6400 字节，1 秒 5 片
SLICE_BYTES = 6400
KEEPALIVE_TIMEOUT = 3.0
SILENCE_SLICE = b"\x00" * SLICE_BYTES
NOISE_TEXTS = {"嗯", "闲聊", "啊", "呃"}
_PUNCT = set("。！？!?.,，、；;：: “”\"'（）() ")
_FLUSH = object()


def normalize_asr(text: str) -> str:
    return "".join(char for char in (text or "").strip() if char not in _PUNCT)


def same_utterance(left: str, right: str) -> bool:
    """标点不同、或一段被另一段包含，都算同一句，避免字幕叠两层。"""
    first = normalize_asr(left)
    second = normalize_asr(right)
    if not first or not second:
        return False
    return first == second or first in second or second in first


class PcmSlicer:
    """把零散 PCM 聚成腾讯云要求的 200ms 分片。"""

    def __init__(self, slice_bytes: int = SLICE_BYTES) -> None:
        self.slice_bytes = max(1600, int(slice_bytes))
        self.buffer = bytearray()

    def add(self, pcm: bytes) -> list[bytes]:
        if not pcm:
            return []
        self.buffer.extend(pcm)
        ready: list[bytes] = []
        while len(self.buffer) >= self.slice_bytes:
            ready.append(bytes(self.buffer[: self.slice_bytes]))
            del self.buffer[: self.slice_bytes]
        return ready

    def flush(self) -> bytes | None:
        if not self.buffer:
            return None
        tail = bytes(self.buffer)
        self.buffer.clear()
        if len(tail) < 1600:
            tail = tail + b"\x00" * (1600 - len(tail))
        return tail


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_ok(code: object) -> bool:
    return code is None or code == 0 or str(code) == "0"


class TencentASRService(WebsocketSTTService):
    """Pipecat WebsocketSTTService：腾讯云实时识别 WebSocket v2。"""

    def __init__(
        self,
        app_id: str,
        secret_id: str,
        secret_key: str,
        connect: Connect | None = None,
        *,
        engine_model_type: str = "16k_zh",
        vad_silence_time: int = 240,
        slice_bytes: int = SLICE_BYTES,
        **kwargs,
    ) -> None:
        super().__init__(
            sample_rate=16000,
            audio_passthrough=False,
            keepalive_timeout=None,
            # 实测 VAD 停说后腾讯云 final 约 0.3~0.5s；官网要求把 P99 告诉 turn strategy
            ttfs_p99_latency=0.25,
            settings=STTSettings(model=engine_model_type, language=Language.ZH),
            **kwargs,
        )
        self._app_id = str(app_id)
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._engine_model_type = engine_model_type
        self._vad_silence_time = vad_silence_time
        self._connect_fn = connect
        self._slicer = PcmSlicer(slice_bytes)
        self._audio_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=128)
        self._writer_task = None
        self._receive_task = None
        self._provider_error_sent = False
        self._last_interim = ""
        self._promoted_text = ""
        self._last_emitted = ""

    async def setup(self, setup) -> None:
        await super().setup(setup)
        await self._connect()

    async def _connect(self) -> None:
        await super()._connect()
        await self._connect_websocket()
        if self._websocket is None:
            return
        if self._writer_task is None:
            self._writer_task = self.create_task(self._writer(), name="tencent_asr_writer")
        if self._receive_task is None:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error),
                name="tencent_asr_reader",
            )

    async def _disconnect(self) -> None:
        await super()._disconnect()
        if self._writer_task is not None:
            await self.cancel_task(self._writer_task)
            self._writer_task = None
        if self._receive_task is not None:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()

    async def _connect_websocket(self) -> None:
        if self._websocket is not None:
            return
        now = int(time.time())
        url = build_asr_ws_url(
            app_id=self._app_id,
            secret_id=self._secret_id,
            secret_key=self._secret_key,
            voice_id=uuid.uuid4().hex,
            timestamp=now,
            expired=now + 24 * 60 * 60,
            nonce=random.randint(1, 1_000_000_000),
            engine_model_type=self._engine_model_type,
            vad_silence_time=self._vad_silence_time,
            filter_modal=1,
            filter_punc=0,
            convert_num_mode=1,
        )
        try:
            if self._connect_fn is not None:
                self._websocket = await self._connect_fn(url)
            else:
                self._websocket = await self._websocket_connect(
                    url,
                    open_timeout=20,
                    ping_interval=10,
                    ping_timeout=10,
                )
            await self._call_event_handler("on_connected")
            logger.info("TencentASR websocket connected")
        except Exception as exc:
            self._websocket = None
            await self._emit_client_error(exc)
            await self._call_event_handler("on_connection_error", str(exc))

    async def _disconnect_websocket(self) -> None:
        ws = self._websocket
        self._websocket = None
        self._slicer.buffer.clear()
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"type": "end"}))
        except Exception:
            pass
        try:
            await ws.close()
        except Exception as exc:
            if not is_abrupt_close(exc):
                logger.warning(f"TencentASR close: {exc}")
        await self._call_event_handler("on_disconnected")

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if self._websocket is None:
            await self._connect()
        if audio:
            try:
                self._audio_queue.put_nowait(audio)
            except asyncio.QueueFull:
                _ = self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio)
        yield None

    async def _handle_vad_user_started_speaking(self, frame) -> None:
        self._last_interim = ""
        self._promoted_text = ""
        self._last_emitted = ""
        await super()._handle_vad_user_started_speaking(frame)

    async def _handle_vad_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame) -> None:
        try:
            self._audio_queue.put_nowait(_FLUSH)
        except asyncio.QueueFull:
            pass
        # 腾讯云还要再等自己的 VAD 才出 final。官网 turn strategy 要等 finalized 才推 LLM，
        # 把最新 interim 提成 final，先让大模型跑起来。
        await self.promote_last_interim()
        await super()._handle_vad_user_stopped_speaking(frame)

    async def promote_last_interim(self) -> None:
        text = (self._last_interim or "").strip()
        if not text:
            return
        cleaned = text.rstrip("。！？!?.,，")
        if cleaned in NOISE_TEXTS:
            return
        if self._last_emitted and same_utterance(text, self._last_emitted):
            return
        logger.info(f"TencentASR promote interim: {text}")
        self._promoted_text = text
        self._last_emitted = text
        await self.push_frame(TranscriptionFrame(text, "user", _timestamp(), finalized=True))

    async def _writer(self) -> None:
        while self._websocket is not None and not self._disconnecting:
            try:
                data = await asyncio.wait_for(self._audio_queue.get(), timeout=KEEPALIVE_TIMEOUT)
            except asyncio.TimeoutError:
                if self._websocket is not None and not self._slicer.buffer:
                    await self._send_pcm(SILENCE_SLICE)
                continue
            except asyncio.CancelledError:
                return
            if data is _FLUSH:
                tail = self._slicer.flush()
                if tail:
                    await self._send_pcm(tail)
                continue
            if not isinstance(data, (bytes, bytearray)):
                continue
            for chunk in self._slicer.add(bytes(data)):
                await self._send_pcm(chunk)

    async def _send_pcm(self, pcm: bytes) -> None:
        ws = self._websocket
        if ws is None or not pcm:
            return
        try:
            await ws.send(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"TencentASR send failed: {exc}")
            self._websocket = None

    async def _receive_messages(self) -> None:
        ws = self._websocket
        if ws is None:
            return
        async for message in ws:
            await self.handle_tencent_message(message)

    async def handle_tencent_message(self, message: object) -> None:
        if isinstance(message, (bytes, bytearray)):
            text = bytes(message).decode("utf-8", errors="ignore")
            if not text.startswith("{"):
                return
            message = text
        if not isinstance(message, str):
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            await self._emit_client_error(exc)
            return
        if not isinstance(payload, dict):
            return
        if not _code_ok(payload.get("code", 0)):
            self._provider_error_sent = True
            await self._emit_client_error(str(payload.get("message") or f"asr error {payload.get('code')}"))
            return
        result = payload.get("result")
        items = result if isinstance(result, list) else [result]
        for item in items:
            if isinstance(item, dict):
                await self.emit_tencent_result(item)

    async def emit_tencent_result(self, result: dict) -> None:
        text = result.get("voice_text_str")
        if not isinstance(text, str) or not text:
            return
        try:
            slice_type = int(result["slice_type"])
        except (KeyError, TypeError, ValueError):
            return
        timestamp = _timestamp()
        if slice_type in (0, 1):
            self._last_interim = text
            await self.push_frame(InterimTranscriptionFrame(text, "user", timestamp))
            return
        if slice_type != 2:
            return
        cleaned = text.strip().rstrip("。！？!?.,，")
        if cleaned in NOISE_TEXTS:
            return
        if self._promoted_text and same_utterance(text, self._promoted_text):
            self._promoted_text = ""
            self._last_interim = ""
            return
        if self._last_emitted and same_utterance(text, self._last_emitted):
            self._last_interim = ""
            return
        logger.info(f"TencentASR final: {text}")
        self._last_interim = ""
        self._promoted_text = ""
        self._last_emitted = text
        # 只发最终转写，不要再塞 UserStoppedSpeakingFrame，否则 Pipecat 会当成新一轮并打断 LLM
        await self.push_frame(TranscriptionFrame(text, "user", timestamp, finalized=True))

    async def _emit_client_error(self, error: object) -> None:
        if is_abrupt_close(error):
            if self._provider_error_sent:
                return
            self._provider_error_sent = True
            message = "腾讯云识别连接被中断，请检查 SecretId 是否有效"
        else:
            message = error if isinstance(error, str) else str(error)
            if not message:
                message = "腾讯云识别连接失败"
        await self.push_frame(client_message({"type": "error", "message": message}))
        await self.push_frame(ErrorFrame(error=message), FrameDirection.DOWNSTREAM)
