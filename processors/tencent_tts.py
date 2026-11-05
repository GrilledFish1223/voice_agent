from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, InterruptionFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import InterruptibleTTSService, TextAggregationMode
from pipecat.transcriptions.language import Language

from voice_agent.processors.sentence_split import FirstClauseAggregator
from voice_agent.processors.socket_errors import is_abrupt_close
from voice_agent.processors.tencent_auth import build_tts_ws_url
from voice_agent.processors.ws_serializer import client_message

Connect = Callable[[str], Awaitable[object]]
SAMPLE_RATE = 16000


def _query_number(value: float) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _code_ok(code: object) -> bool:
    return code is None or code == 0 or str(code) == "0"


def _ws_open(ws) -> bool:
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        return str(state) == "OPEN" or getattr(state, "name", "") == "OPEN"
    return not bool(getattr(ws, "closed", False))


class TencentTTSService(InterruptibleTTSService):
    """Pipecat InterruptibleTTSService：腾讯云 stream_wsv2 WebSocket。"""

    def __init__(
        self,
        app_id: str,
        secret_id: str,
        secret_key: str,
        voice_type: int,
        fast_voice_type: str,
        connect: Connect | None = None,
        speed: float = 0,
        volume: float = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            push_stop_frames=True,
            push_start_frame=True,
            text_aggregation_mode=TextAggregationMode.SENTENCE,
            pause_frame_processing=True,
            sample_rate=SAMPLE_RATE,
            # 音频从 receive 循环进 context，不能按“context 任务里没音频”把服务打成不可用
            max_consecutive_zero_audio_contexts=0,
            settings=TTSSettings(
                model="tencent-tts",
                voice=str(voice_type),
                language=Language.ZH,
            ),
            **kwargs,
        )
        self._text_aggregator = FirstClauseAggregator(language="zh")
        self._app_id = str(app_id)
        self._secret_id = secret_id
        self._secret_key = secret_key
        self.voice_type = voice_type
        self.fast_voice_type = fast_voice_type or ""
        self.speed = speed
        self.volume = volume
        self._connect_fn = connect
        self.session_id: str | None = None
        self._receive_task = None
        self._live_voice: tuple[object, str] | None = None
        self._provider_error_sent = False

    def _voice_key(self) -> tuple[object, str]:
        return (self.voice_type, self.fast_voice_type or "")

    async def setup(self, setup) -> None:
        await super().setup(setup)
        await self._connect()

    async def _connect(self) -> None:
        await super()._connect()
        await self._connect_websocket()
        if self._websocket and self._receive_task is None:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self) -> None:
        await super()._disconnect()
        if self._receive_task is not None:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()

    async def _connect_websocket(self) -> None:
        if _ws_open(self._websocket) and self._live_voice == self._voice_key() and self.session_id:
            return
        old = self._websocket
        self._websocket = None
        self.session_id = None
        self._live_voice = None
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self.session_id = str(uuid.uuid4())
        now = int(time.time())
        url = build_tts_ws_url(
            app_id=self._app_id,
            secret_id=self._secret_id,
            secret_key=self._secret_key,
            session_id=self.session_id,
            voice_type=int(self.voice_type),
            fast_voice_type=self.fast_voice_type or "",
            speed=_query_number(self.speed),
            volume=_query_number(self.volume),
            timestamp=now,
            expired=now + 24 * 60 * 60,
        )
        try:
            if self._connect_fn is not None:
                self._websocket = await self._connect_fn(url)
            else:
                self._websocket = await self._websocket_connect(url, open_timeout=20)
            self._live_voice = self._voice_key()
            await self.set_usable(True)
            await self._call_event_handler("on_connected")
            logger.info("TencentTTS websocket connected")
        except Exception as exc:
            self._websocket = None
            self._live_voice = None
            await self._emit_client_error(exc)
            await self._call_event_handler("on_connection_error", str(exc))

    async def _disconnect_websocket(self) -> None:
        ws = self._websocket
        self._websocket = None
        self._live_voice = None
        self.session_id = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception as exc:
            if not is_abrupt_close(exc):
                logger.warning(f"TencentTTS close: {exc}")
        await self._call_event_handler("on_disconnected")

    async def _send_action(self, action: str, data: str = "") -> None:
        if not _ws_open(self._websocket) or not self.session_id:
            return
        payload = {
            "session_id": self.session_id,
            "message_id": str(uuid.uuid4()),
            "action": action,
        }
        if data or action == "ACTION_COMPLETE":
            payload["data"] = data
        await self._websocket.send(json.dumps(payload))

    async def _handle_interruption(self, frame: InterruptionFrame, direction: FrameDirection) -> None:
        try:
            await self._send_action("ACTION_RESET")
        except Exception:
            pass
        await super()._handle_interruption(frame, direction)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        text = (text or "").strip()
        if not text:
            yield None
            return
        # 不要在合成路径上调 _disconnect()：receive 循环里会把 _disconnecting 置位，随后自动重连被跳过
        await self._ensure_session()
        if not _ws_open(self._websocket) or not self.session_id:
            yield ErrorFrame(error="腾讯云合成连接失败")
            return
        logger.debug(f"TencentTTS synthesis: {text}")
        await self.start_ttfb_metrics()
        try:
            await self._send_action("ACTION_SYNTHESIS", text)
        except Exception as exc:
            await self._emit_client_error(exc)
            yield ErrorFrame(error=str(exc))
            return
        yield None

    async def flush_audio(self, context_id: str | None = None) -> None:
        try:
            await self._send_action("ACTION_COMPLETE")
        except Exception as exc:
            logger.warning(f"TencentTTS ACTION_COMPLETE failed: {exc}")

    async def _receive_messages(self) -> None:
        ws = self._websocket
        if ws is None:
            return
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                await self.stop_ttfb_metrics()
                self._consecutive_zero_audio_contexts = 0
                context_id = self.get_active_audio_context_id()
                frame = TTSAudioRawFrame(
                    audio=bytes(message),
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                )
                frame.context_id = context_id
                # 官方 WebSocket TTS 都把音频记进 audio context，否则 context 会被当成空的
                if context_id:
                    await self.append_to_audio_context(context_id, frame)
                else:
                    await self.push_frame(frame)
                continue
            if not isinstance(message, str):
                continue
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if not _code_ok(payload.get("code", 0)):
                self._provider_error_sent = True
                await self._emit_client_error(
                    str(payload.get("message") or f"tts error {payload.get('code')}")
                )
                continue
            if payload.get("final") == 1 or payload.get("final") == "1":
                # stream_wsv2 一轮会话用完就必须换新 session。关掉当前连接，
                # receive 循环会自动重连；不要走 _disconnect()，否则 _disconnecting 会拦住重连。
                logger.debug("TencentTTS session final, closing for reconnect")
                self.session_id = None
                self._live_voice = None
                ws = self._websocket
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                return

    async def _ensure_session(self) -> None:
        if _ws_open(self._websocket) and self.session_id and self._live_voice == self._voice_key():
            return
        if getattr(self, "_reconnect_in_progress", False):
            for _ in range(25):
                await asyncio.sleep(0.02)
                if _ws_open(self._websocket) and self.session_id:
                    return
        await self._connect_websocket()
        if self._websocket and (self._receive_task is None or self._receive_task.done()):
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _emit_client_error(self, error: object) -> None:
        if is_abrupt_close(error):
            if self._provider_error_sent:
                return
            self._provider_error_sent = True
            message = "腾讯云合成连接被中断，请检查 SecretId 是否有效"
        else:
            message = error if isinstance(error, str) else str(error)
            if not message:
                message = "腾讯云合成连接失败"
        await self.push_frame(client_message({"type": "error", "message": message}))
