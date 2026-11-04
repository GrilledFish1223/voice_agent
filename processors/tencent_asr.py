from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import websockets
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_agent.processors.tencent_auth import build_asr_ws_url
from voice_agent.processors.ws_serializer import client_message

Connect = Callable[[str], Awaitable[object]]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_ok(code: object) -> bool:
    return code is None or code == 0 or str(code) == "0"


def _is_real_cancel() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _default_connect(url: str) -> object:
    return await websockets.connect(url)


class TencentASRService(FrameProcessor):
    """腾讯云实时识别。连接由 connect(url) 注入，默认才走 websockets。"""

    def __init__(
        self,
        app_id: str,
        secret_id: str,
        secret_key: str,
        connect: Connect | None = None,
        *,
        engine_model_type: str = "16k_zh",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._app_id = str(app_id)
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._engine_model_type = engine_model_type
        self._connect = connect or _default_connect
        self._ws = None
        self._closing = False
        self._read_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # StartFrame 会让基类创建处理任务。Pipeline 先注入 TaskManager；单独调用时跳过，避免抛出事件循环。
        if self._task_manager is not None:
            await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, StartFrame):
            self._schedule_read(uuid.uuid4().hex)
        elif isinstance(frame, InputAudioRawFrame):
            await self.send_audio(frame.audio)
            return
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self.finish()
        await self.push_frame(frame, direction)

    def _schedule_read(self, voice_id: str) -> None:
        if self._read_task is not None and not self._read_task.done():
            return
        self._read_task = asyncio.create_task(self.connect_and_read(voice_id))

    async def connect_and_read(self, voice_id: str) -> None:
        ws = None
        try:
            now = int(time.time())
            url = build_asr_ws_url(
                app_id=self._app_id,
                secret_id=self._secret_id,
                secret_key=self._secret_key,
                voice_id=voice_id,
                timestamp=now,
                expired=now + 24 * 60 * 60,
                nonce=random.randint(1, 1_000_000_000),
                engine_model_type=self._engine_model_type,
            )
            ws = await self._connect(url)
        except asyncio.CancelledError:
            if ws is not None:
                self._ws = ws
            if _is_real_cancel():
                raise
            return
        except Exception as exc:
            self._ws = None
            await self._emit_error(exc)
            return
        self._ws = ws
        if self._closing:
            return
        await self._consume(ws)

    async def send_audio(self, pcm: bytes) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(pcm)
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
        except Exception as exc:
            await self._emit_error(exc)

    async def finish(self) -> None:
        self._closing = True
        await self._stop_reader()
        ws = self._ws
        if ws is None:
            self._closing = False
            return
        try:
            try:
                await ws.send(json.dumps({"type": "end"}))
            except asyncio.CancelledError:
                if _is_real_cancel():
                    raise
            except Exception as exc:
                await self._emit_error(exc)
            try:
                await ws.close()
            except asyncio.CancelledError:
                if _is_real_cancel():
                    raise
            except Exception as exc:
                await self._emit_error(exc)
        finally:
            if self._ws is ws:
                self._ws = None
            self._closing = False

    async def _consume(self, ws) -> None:
        while True:
            try:
                message = await ws.recv()
            except asyncio.CancelledError:
                if _is_real_cancel():
                    raise
                return
            except Exception as exc:
                if not self._closing:
                    await self._emit_error(exc)
                return
            try:
                await self._on_message(message)
            except Exception as exc:
                await self._emit_error(exc)

    async def _on_message(self, message: object) -> None:
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
            await self._emit_error(exc)
            return
        if not isinstance(payload, dict):
            return
        if not _code_ok(payload.get("code", 0)):
            await self._emit_error(str(payload.get("message") or f"asr error {payload.get('code')}"))
            return
        result = payload.get("result")
        items = result if isinstance(result, list) else [result]
        for item in items:
            if isinstance(item, dict):
                await self._emit_slice(item)

    async def _emit_slice(self, result: dict) -> None:
        text = result.get("voice_text_str")
        if not isinstance(text, str) or not text:
            return
        try:
            slice_type = int(result["slice_type"])
        except (KeyError, TypeError, ValueError):
            return
        timestamp = _timestamp()
        if slice_type in (0, 1):
            frame: Frame = InterimTranscriptionFrame(text, "user", timestamp)
        elif slice_type == 2:
            frame = TranscriptionFrame(text, "user", timestamp)
        else:
            return
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _stop_reader(self) -> None:
        task = self._read_task
        self._read_task = None
        if task is None or task is asyncio.current_task() or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
        except Exception:
            return

    async def _emit_error(self, error: object) -> None:
        message = error if isinstance(error, str) else str(error)
        if not message:
            message = "腾讯云识别连接失败"
        try:
            await self.push_frame(
                client_message({"type": "error", "message": message}),
                FrameDirection.DOWNSTREAM,
            )
        except asyncio.CancelledError:
            if _is_real_cancel():
                raise
        except Exception:
            return
