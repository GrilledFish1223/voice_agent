from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable

import websockets
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

try:
    from pipecat.frames.frames import TTSAudioRawFrame
except ImportError:  # 旧版本只有输出音频帧
    from pipecat.frames.frames import OutputAudioRawFrame as TTSAudioRawFrame

try:
    from pipecat.frames.frames import StartInterruptionFrame
except ImportError:  # Pipecat 1.x 只保留 InterruptionFrame
    StartInterruptionFrame = None  # type: ignore[misc,assignment]

from voice_agent.processors.sentence_split import SentenceBuffer
from voice_agent.processors.tencent_auth import build_tts_ws_url
from voice_agent.processors.ws_serializer import ClientEventFrame

Connect = Callable[[str], Awaitable[object]]
SAMPLE_RATE = 16000


def _query_number(value: float) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _code_ok(code: object) -> bool:
    return code is None or code == 0 or str(code) == "0"


def _is_real_cancel() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _is_interruption(frame: Frame) -> bool:
    kinds: tuple[type, ...] = (InterruptionFrame,)
    if isinstance(StartInterruptionFrame, type):
        kinds = (InterruptionFrame, StartInterruptionFrame)
    return isinstance(frame, kinds)


async def _default_connect(url: str) -> object:
    return await websockets.connect(url)


class TencentTTSService(FrameProcessor):
    """腾讯云流式合成。首句立即送出，打断时丢掉未播放的文本和音频。"""

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
        super().__init__(**kwargs)
        self._app_id = str(app_id)
        self._secret_id = secret_id
        self._secret_key = secret_key
        self.voice_type = voice_type
        self.fast_voice_type = fast_voice_type or ""
        self.speed = speed
        self.volume = volume
        self._connect = connect or _default_connect
        self._sentences = SentenceBuffer()
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self.session_id: str | None = None
        self._live_voice: tuple[object, str] | None = None
        self._accept_audio = False
        self._turn_token = 0
        self._closing = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # StartFrame 会让基类创建处理任务。Pipeline 先注入 TaskManager；单独调用时跳过，避免抛出事件循环。
        if self._task_manager is not None:
            await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, StartFrame):
            await self.connect_session()
        elif isinstance(frame, LLMTextFrame):
            await self.submit_text(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._flush_tail()
        elif isinstance(frame, EndFrame):
            await self._flush_tail()
            await self._close_socket()
        elif isinstance(frame, CancelFrame):
            await self.interrupt()
            await self._close_socket()
        elif _is_interruption(frame):
            await self.interrupt()
        await self.push_frame(frame, direction)

    def _voice_key(self) -> tuple[object, str]:
        return (self.voice_type, self.fast_voice_type or "")

    async def connect_session(self) -> None:
        if self._ws is not None and self._live_voice == self._voice_key():
            return
        if self._ws is not None:
            await self._close_socket()
        self.session_id = str(uuid.uuid4())
        self._ready.clear()
        self._accept_audio = False
        now = int(time.time())
        try:
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
            self._ws = await self._connect(url)
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
            return
        except Exception as exc:
            self._ws = None
            self._live_voice = None
            await self._emit_error(exc)
            return
        self._live_voice = self._voice_key()
        self._reader = asyncio.create_task(self._read_loop(self._ws))

    async def submit_text(self, text: str) -> None:
        token = self._turn_token
        ready = self._sentences.push(text or "")
        if ready:
            if not await self._synthesize(ready[0], token):
                return
            for sentence in ready[1:]:
                if not await self._synthesize(sentence, token):
                    return
        if token != self._turn_token:
            return
        for sentence in self._sentences.push(""):
            if not await self._synthesize(sentence, token):
                return

    async def interrupt(self) -> None:
        self._turn_token += 1
        self._sentences.reset()
        self._accept_audio = False
        ws = self._ws
        if ws is not None and self.session_id:
            payload = {
                "session_id": self.session_id,
                "message_id": str(uuid.uuid4()),
                "action": "ACTION_RESET",
            }
            try:
                await ws.send(json.dumps(payload))
            except asyncio.CancelledError:
                if _is_real_cancel():
                    raise
            except Exception as exc:
                await self._emit_error(exc)
        await self._close_socket()
        await self.connect_session()

    async def _flush_tail(self) -> None:
        token = self._turn_token
        for sentence in self._sentences.flush():
            if not await self._synthesize(sentence, token):
                return

    async def _synthesize(self, sentence: str, token: int) -> bool:
        if token != self._turn_token or not sentence:
            return False
        if not await self._ensure_connected():
            return False
        if token != self._turn_token or self._ws is None:
            return False
        if not await self._wait_ready():
            return False
        if token != self._turn_token or self._ws is None or not self.session_id:
            return False
        ws = self._ws
        payload = {
            "session_id": self.session_id,
            "message_id": str(uuid.uuid4()),
            "action": "ACTION_SYNTHESIS",
            "data": sentence,
        }
        try:
            await ws.send(json.dumps(payload))
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
            return False
        except Exception as exc:
            await self._emit_error(exc)
            return False
        # 发送之后再让读循环把已经进队的旧音频丢掉，然后才开始播放。
        await asyncio.sleep(0)
        if token != self._turn_token or self._ws is not ws:
            return False
        self._accept_audio = True
        return True

    async def _ensure_connected(self) -> bool:
        if self._ws is not None and self._live_voice != self._voice_key():
            await self._close_socket()
        if self._ws is None:
            await self.connect_session()
        return self._ws is not None

    async def _wait_ready(self) -> bool:
        if self._ready.is_set():
            return self._ws is not None
        reader = self._reader
        if reader is None:
            return False
        waiter = asyncio.create_task(self._ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {waiter, reader},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
            if _is_real_cancel():
                raise
            return False
        if waiter not in done:
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
        return self._ready.is_set() and self._ws is not None

    async def _read_loop(self, ws) -> None:
        try:
            while True:
                message = await ws.recv()
                try:
                    await self._on_server_message(message)
                except Exception as exc:
                    await self._emit_error(exc)
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
        except Exception as exc:
            if self._ws is ws:
                self._ws = None
                self._ready.clear()
                self._live_voice = None
            if not self._closing:
                await self._emit_error(exc)

    async def _on_server_message(self, message: object) -> None:
        if isinstance(message, (bytes, bytearray)):
            if not self._accept_audio:
                return
            await self.push_frame(
                TTSAudioRawFrame(audio=bytes(message), sample_rate=SAMPLE_RATE, num_channels=1),
                FrameDirection.DOWNSTREAM,
            )
            return
        if not isinstance(message, str):
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            await self._emit_error(exc)
            return
        if not isinstance(payload, dict):
            return
        if not _code_ok(payload.get("code", 0)):
            await self._emit_error(str(payload.get("message") or f"tts error {payload.get('code')}"))
            return
        if payload.get("ready") == 1:
            self._ready.set()
        # final == 1 只表示这一次套接字上的合成结束，对话继续。
        if payload.get("heartbeat") == 1:
            return

    async def _close_socket(self) -> None:
        ws = self._ws
        reader = self._reader
        self._ws = None
        self._reader = None
        self._ready.clear()
        self._live_voice = None
        self._closing = True
        try:
            if reader is not None and reader is not asyncio.current_task():
                if not reader.done():
                    reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            if ws is not None:
                try:
                    await ws.close()
                except Exception as exc:
                    await self._emit_error(exc)
        finally:
            self._closing = False

    async def _emit_error(self, error: object) -> None:
        message = error if isinstance(error, str) else str(error)
        if not message:
            message = "腾讯云合成连接失败"
        try:
            await self.push_frame(
                ClientEventFrame(payload={"type": "error", "message": message}),
                FrameDirection.DOWNSTREAM,
            )
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
        except Exception:
            return
