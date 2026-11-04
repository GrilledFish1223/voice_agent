import asyncio
import json

import pytest

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
