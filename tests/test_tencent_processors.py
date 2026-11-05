import asyncio
import json

import pytest
from pipecat.frames.frames import TranscriptionFrame, UserStoppedSpeakingFrame

from voice_agent.processors.tencent_asr import PcmSlicer, TencentASRService, same_utterance
from voice_agent.processors.tencent_tts import TencentTTSService


class FakeSocket:
    def __init__(self, incoming: list[str | bytes] | None = None):
        self.sent = []
        self.closed = False
        self._incoming = asyncio.Queue()
        for item in incoming or []:
            self._incoming.put_nowait(item)

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        item = await self._incoming.get()
        if item is None:
            raise asyncio.CancelledError
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        self.sent.append("closed")


def _asr(**kwargs) -> TencentASRService:
    return TencentASRService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        **kwargs,
    )


def _tts(connect=None) -> TencentTTSService:
    return TencentTTSService(
        app_id="100",
        secret_id="sid",
        secret_key="test-secret",
        voice_type=101001,
        fast_voice_type="",
        connect=connect,
    )


def test_pcm_slicer_waits_until_200ms():
    # 测试场景：80 个 80 字节小包凑满 6400 字节
    # 预期结果：只产出 1 个 200ms 分片
    slicer = PcmSlicer()
    for _ in range(79):
        assert slicer.add(b"\x01\x00" * 40) == []
    chunks = slicer.add(b"\x01\x00" * 40)
    assert len(chunks) == 1
    assert len(chunks[0]) == 6400


def test_pcm_slicer_flush_pads_short_tail():
    slicer = PcmSlicer()
    slicer.add(b"\x01\x00" * 10)
    tail = slicer.flush()
    assert tail is not None
    assert len(tail) == 1600


@pytest.mark.asyncio
async def test_asr_final_is_finalized_and_does_not_stop_user_turn():
    # 测试场景：腾讯云返回 slice_type=2
    # 预期结果：只推 finalized 转写，不再推 UserStoppedSpeakingFrame，避免打断 LLM
    service = _asr()
    seen = []

    async def capture(frame, direction=None):
        seen.append(frame)

    service.push_frame = capture
    await service.emit_tencent_result({"slice_type": 2, "voice_text_str": "你好"})
    finals = [frame for frame in seen if isinstance(frame, TranscriptionFrame)]
    assert len(finals) == 1
    assert finals[0].text == "你好"
    assert finals[0].finalized is True
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in seen)


@pytest.mark.asyncio
async def test_asr_promotes_interim_when_vad_stops():
    # 测试场景：VAD 已停，腾讯云 final 还没到
    # 预期结果：先把最新 interim 提成 finalized，让 LLM 立刻开跑
    service = _asr()
    service._last_interim = "你好呀"
    seen = []

    async def capture(frame, direction=None):
        seen.append(frame)

    service.push_frame = capture
    await service.promote_last_interim()
    finals = [frame for frame in seen if isinstance(frame, TranscriptionFrame)]
    assert len(finals) == 1
    assert finals[0].text == "你好呀"
    assert finals[0].finalized is True

    seen.clear()
    await service.emit_tencent_result({"slice_type": 2, "voice_text_str": "你好呀"})
    assert [frame for frame in seen if isinstance(frame, TranscriptionFrame)] == []

    seen.clear()
    await service.emit_tencent_result({"slice_type": 2, "voice_text_str": "你好呀。"})
    assert [frame for frame in seen if isinstance(frame, TranscriptionFrame)] == []


def test_same_utterance_ignores_punctuation_and_overlap():
    assert same_utterance("我不要", "我不要。")
    assert same_utterance("不要", "我不要我不要")
    assert not same_utterance("放首歌", "今天天气不错")


@pytest.mark.asyncio
async def test_asr_interim_does_not_finalize():
    service = _asr()
    seen = []

    async def capture(frame, direction=None):
        seen.append(frame)

    service.push_frame = capture
    await service.emit_tencent_result({"slice_type": 1, "voice_text_str": "你"})
    assert not any(isinstance(frame, TranscriptionFrame) for frame in seen)
    assert seen[0].text == "你"


@pytest.mark.asyncio
async def test_asr_filters_noise_final():
    service = _asr()
    seen = []

    async def capture(frame, direction=None):
        seen.append(frame)

    service.push_frame = capture
    await service.emit_tencent_result({"slice_type": 2, "voice_text_str": "嗯"})
    assert seen == []


@pytest.mark.asyncio
async def test_asr_keeps_provider_error():
    # 测试场景：腾讯云先返回密钥错误
    # 预期结果：页面只看到中文密钥错误
    service = _asr()
    messages = []

    async def capture(frame, direction=None):
        payload = getattr(frame, "message", None)
        if isinstance(payload, dict) and payload.get("type") == "error":
            messages.append(payload["message"])

    service.push_frame = capture
    await service.handle_tencent_message(json.dumps({"code": 4001, "message": "密钥不存在"}))
    assert messages == ["密钥不存在"]


@pytest.mark.asyncio
async def test_run_stt_queues_audio_without_sending_tiny_frames():
    service = _asr()
    socket = FakeSocket()
    service._websocket = socket
    async for _ in service.run_stt(b"\x01\x00" * 40):
        pass
    assert socket.sent == []
    queued = service._audio_queue.get_nowait()
    assert queued == b"\x01\x00" * 40


@pytest.mark.asyncio
async def test_tts_run_tts_sends_action_synthesis():
    socket = FakeSocket()
    service = _tts()
    service._websocket = socket
    service.session_id = "sess"
    service._live_voice = service._voice_key()

    async def capture(frame, direction=None):
        return None

    service.push_frame = capture
    async for _ in service.run_tts("今天天气不错，", "ctx-1"):
        pass
    synthesis = [json.loads(item) for item in socket.sent if isinstance(item, str) and "ACTION_" in item]
    assert synthesis[0]["action"] == "ACTION_SYNTHESIS"
    assert synthesis[0]["data"] == "今天天气不错，"


@pytest.mark.asyncio
async def test_tts_flush_sends_action_complete():
    socket = FakeSocket()
    service = _tts()
    service._websocket = socket
    service.session_id = "sess"
    await service.flush_audio("ctx-1")
    actions = [json.loads(item)["action"] for item in socket.sent if isinstance(item, str)]
    assert actions == ["ACTION_COMPLETE"]


@pytest.mark.asyncio
async def test_tts_receive_appends_audio_and_keeps_session_on_final():
    # 测试场景：收到 PCM 和 final=1
    # 预期结果：音频进 audio context，不在 receive 循环里断开连接
    socket = FakeSocket([b"\x00\x01" * 8, json.dumps({"code": 0, "final": 1}), None])
    service = _tts()
    service._websocket = socket
    service.session_id = "sess"
    appended = []

    async def capture_append(context_id, frame):
        appended.append((context_id, frame))

    service.get_active_audio_context_id = lambda: "ctx-1"
    service.append_to_audio_context = capture_append

    async def fail_disconnect():
        raise AssertionError("final 不应断开连接")

    service._disconnect = fail_disconnect
    await service._receive_messages()
    assert len(appended) == 1
    assert appended[0][0] == "ctx-1"
    assert appended[0][1].audio == b"\x00\x01" * 8
    assert service.session_id is None
    assert socket.closed is True


@pytest.mark.asyncio
async def test_tts_sends_action_reset():
    socket = FakeSocket()
    service = _tts()
    service._websocket = socket
    service.session_id = "sess"
    await service._send_action("ACTION_RESET")
    actions = [json.loads(item)["action"] for item in socket.sent if isinstance(item, str)]
    assert actions == ["ACTION_RESET"]
