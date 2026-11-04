import asyncio
import json

import pytest
from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection

from voice_agent.processors.tencent_asr import TencentASRService
from voice_agent.processors.tencent_tts import TencentTTSService


class FakeSocket:
    def __init__(self, incoming: list[str | bytes]):
        self.sent = []
        self._incoming = asyncio.Queue()
        for item in incoming:
            self._incoming.put_nowait(item)
        self._incoming.put_nowait(None)

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        item = await self._incoming.get()
        if item is None:
            raise asyncio.CancelledError
        return item

    async def close(self):
        self.sent.append("closed")


@pytest.mark.asyncio
async def test_asr_final_slice_emits_transcription_and_end():
    socket = FakeSocket([
        json.dumps({"code": 0, "result": {"slice_type": 2, "voice_text_str": "你好"}}),
    ])

    async def connect(_url):
        return socket

    service = TencentASRService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        connect=connect,
    )
    texts = []

    async def capture(frame, direction):
        if getattr(frame, "text", None):
            texts.append(frame.text)

    service.push_frame = capture
    await service.connect_and_read("vid")
    await service.send_audio(b"\x00\x01")
    await service.finish()
    assert texts == ["你好"]
    assert b"\x00\x01" in socket.sent
    assert json.dumps({"type": "end"}) in socket.sent


@pytest.mark.asyncio
async def test_tts_cuts_first_clause_and_resets_on_interrupt():
    socket = FakeSocket([
        json.dumps({"code": 0, "ready": 1, "heartbeat": 0, "final": 0}),
        b"\x01\x02",
    ])

    async def connect(_url):
        return socket

    service = TencentTTSService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        voice_type=101001,
        fast_voice_type="",
        connect=connect,
    )
    audio = []

    async def capture(frame, direction):
        if getattr(frame, "audio", None):
            audio.append(frame.audio)

    service.push_frame = capture
    await service.connect_session()
    await service.submit_text("今天天气不错，我们出发吧。")
    await asyncio.sleep(0)
    synthesis = [json.loads(item) for item in socket.sent if isinstance(item, str) and "ACTION_" in item]
    assert synthesis[0]["action"] == "ACTION_SYNTHESIS"
    assert synthesis[0]["data"] == "今天天气不错，"
    await service.interrupt()
    actions = [json.loads(item)["action"] for item in socket.sent if isinstance(item, str) and "ACTION_" in item]
    assert "ACTION_RESET" in actions


class QueueSocket:
    def __init__(self, incoming: list[str | bytes]):
        self.sent = []
        self.incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        for item in incoming:
            self.incoming.put_nowait(item)

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        item = await self.incoming.get()
        if item is None:
            raise asyncio.CancelledError
        return item

    async def close(self):
        self.sent.append("closed")


@pytest.mark.asyncio
async def test_interrupt_drops_stale_audio_until_next_synthesis():
    sockets: list[QueueSocket] = []

    async def connect(_url):
        sock = QueueSocket([
            json.dumps({"code": 0, "ready": 1, "heartbeat": 0, "final": 0}),
        ])
        sockets.append(sock)
        return sock

    service = TencentTTSService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        voice_type=101001,
        fast_voice_type="",
        connect=connect,
    )
    audio = []

    async def capture(frame, direction):
        if getattr(frame, "audio", None):
            audio.append(frame.audio)

    service.push_frame = capture
    await service.connect_session()
    await service.submit_text("今天天气不错，")
    await service.interrupt()
    old = sockets[0]
    old.incoming.put_nowait(b"STALE")
    await service.submit_text("新的一句。")
    new = sockets[-1]
    new.incoming.put_nowait(b"FRESH")
    await asyncio.sleep(0)
    assert old is not new
    assert b"STALE" not in audio
    assert b"FRESH" in audio
    new.incoming.put_nowait(None)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_llm_text_continues_downstream():
    socket = FakeSocket([
        json.dumps({"code": 0, "ready": 1, "heartbeat": 0, "final": 0}),
    ])

    async def connect(_url):
        return socket

    service = TencentTTSService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        voice_type=101001,
        fast_voice_type="",
        connect=connect,
    )
    seen = []

    async def capture(frame, direction):
        seen.append(frame)

    service.push_frame = capture
    frame = LLMTextFrame(text="今天天气不错，")
    await service.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert frame in seen


class LateSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.Future()

    async def close(self):
        self.sent.append("closed")


@pytest.mark.asyncio
async def test_finish_closes_socket_opened_after_finish_starts():
    started = asyncio.Event()
    sockets: list[LateSocket] = []

    async def connect(_url):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        sock = LateSocket()
        sockets.append(sock)
        return sock

    service = TencentASRService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        connect=connect,
    )

    async def capture(frame, direction):
        return None

    service.push_frame = capture
    service._schedule_read("vid")
    await started.wait()
    await service.finish()
    assert sockets
    assert json.dumps({"type": "end"}) in sockets[0].sent
    assert "closed" in sockets[0].sent
    assert service._ws is None
