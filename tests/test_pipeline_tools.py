import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice_agent.config import Settings
from voice_agent.pipeline import (
    ConversationMicGate,
    MusicSession,
    MusicStage,
    build_llm_settings,
    build_session,
    build_user_turn_params,
)
from voice_agent.processors.caption_tap import CaptionTap, latency_payload, metrics_payload
from voice_agent.processors.music_mixer import DUCK_GAIN, mix_int16
from voice_agent.processors.ws_serializer import ClientEventFrame
from voice_agent.services.music_library import MusicLibrary, read_pcm_frames


class _Params:
    def __init__(self) -> None:
        self.result: dict = {}

    async def result_callback(self, data):
        self.result.update(data)


def _settings(**overrides) -> Settings:
    data = {
        "tencent_app_id": "",
        "tencent_secret_id": "",
        "tencent_secret_key": "",
        "llm_base_url": "",
        "llm_api_key": "",
        "llm_model": "",
        "host": "127.0.0.1",
        "port": 8765,
    }
    data.update(overrides)
    return Settings(**data)


@pytest.mark.asyncio
async def test_play_music_client_mode_emits_track_url(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    session = MusicSession(mode="client_url", library=library)
    params = _Params()
    await session.play(params, "深海", "")
    messages = []
    while not session.outbound.empty():
        messages.append(session.outbound.get_nowait())
    assert session.session_mode == "music"
    assert messages[0] == {"type": "session_mode", "mode": "music", "playing": True}
    message = messages[1]
    assert message["type"] == "music_control"
    assert message["track"]["id"] == "deep_sea"
    assert message["session_mode"] == "music"
    assert params.result["title"] == "深海冥想"


@pytest.mark.asyncio
async def test_server_mix_play_does_not_enqueue_url(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    session = MusicSession(mode="server_mix", library=library)
    params = _Params()
    await session.play(params, "深海", "")
    assert session.outbound.get_nowait() == {"type": "session_mode", "mode": "music", "playing": True}
    assert session.outbound.empty()
    assert session.playing is True
    assert session.session_mode == "music"
    assert session.current is not None
    assert session.current.id == "deep_sea"
    assert params.result["ok"] is True
    assert params.result["mode"] == "server_mix"


@pytest.mark.asyncio
async def test_control_pause_sets_playing_false(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    session = MusicSession(mode="server_mix", library=library)
    session.playing = True
    params = _Params()
    await session.control(params, "pause", session.volume)
    assert session.playing is False
    assert session.session_mode == "conversation"
    assert session.outbound.get_nowait() == {"type": "session_mode", "mode": "conversation", "playing": False}
    assert session.outbound.empty()
    assert params.result["ok"] is True


def test_server_mix_uses_duck_gain_while_speech_is_active(tmp_path):
    library = MusicLibrary(tmp_path)
    track = library.ensure_seed_tracks()[0]
    stage = MusicStage(MusicSession(mode="server_mix", library=library))
    stage.session.current = track
    stage.session.playing = True
    stage.session.speech_active = True
    pcm = stage.mix_tts_chunk(b"\x00\x01" * 320)
    assert len(pcm) == 640
    music = next(read_pcm_frames(track, frame_bytes=640))
    assert pcm == mix_int16(b"\x00\x01" * 320, music, DUCK_GAIN)


def test_server_mix_volume_zero_silences_music(tmp_path):
    library = MusicLibrary(tmp_path)
    track = library.ensure_seed_tracks()[0]
    stage = MusicStage(MusicSession(mode="server_mix", library=library))
    stage.session.current = track
    stage.session.playing = True
    stage.session.volume = 0
    pcm = stage.mix_tts_chunk(b"\x00\x01" * 320)
    music = next(read_pcm_frames(track, frame_bytes=640))
    assert pcm == mix_int16(b"\x00\x01" * 320, music, 0)


@pytest.mark.asyncio
async def test_caption_tap_emits_final_text_and_marks_speech(tmp_path):
    session = MusicSession(mode="server_mix", library=MusicLibrary(tmp_path))
    tap = CaptionTap(session)
    sent = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        sent.append(frame)

    tap.push_frame = capture
    transcript = TranscriptionFrame("你好", "user", "2026-11-04T00:00:00+00:00")
    await tap.process_frame(transcript, FrameDirection.DOWNSTREAM)
    assert sent[0] is transcript
    assert sent[1].message == {"type": "user_transcript", "text": "你好", "final": True}

    sent.clear()
    await tap.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert session.speech_active is True
    assert sent[-1].message == {"type": "user_speaking", "active": True}

    sent.clear()
    await tap.process_frame(LLMTextFrame("好的"), FrameDirection.DOWNSTREAM)
    assert sent[-1].message == {"type": "bot_output", "text": "好的", "final": False}

    sent.clear()
    await tap.publish({"type": "bot_output", "text": "你好，我在。", "final": True})
    assert sent[0].message == {"type": "bot_output", "text": "你好，我在。", "final": True}


def test_metrics_payload_and_latency_payload():
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import TTFBMetricsData

    frame = MetricsFrame(data=[TTFBMetricsData(processor="OpenAILLMService#0", value=0.321)])
    assert metrics_payload(frame) == {
        "type": "metrics",
        "items": [{"kind": "TTFB", "processor": "OpenAILLMService", "value": 0.321}],
    }
    assert latency_payload(1.25, [{"key": "llm_inference", "label": "LLM inference", "duration_secs": 0.4}]) == {
        "type": "metrics",
        "user_to_bot_secs": 1.25,
        "parts": [{"key": "llm_inference", "label": "LLM inference", "duration_secs": 0.4}],
    }


def test_llm_settings_disable_thinking():
    settings = build_llm_settings("deepseek-flash", "只说一句。")
    assert settings.model == "deepseek-flash"
    assert settings.max_tokens == 80
    assert settings.extra["extra_body"]["thinking"]["type"] == "disabled"


def test_user_turn_params_do_not_stack_extra_silence():
    # 测试场景：只用 VAD 开轮，避免腾讯云中途 final 再开一轮打断 LLM
    # 预期结果：start 只有 VAD，speech timeout 为 0，兜底 2s
    params = build_user_turn_params()
    start = params.user_turn_strategies.start
    assert len(start) == 1
    assert start[0].__class__.__name__ == "VADUserTurnStartStrategy"
    stop = params.user_turn_strategies.stop[0]
    assert stop._user_speech_timeout == 0.0
    assert stop.wait_for_transcript is True
    assert params.user_turn_stop_timeout == 2.0
    assert params.empty_user_turn is None


def test_build_session_rejects_missing_settings_without_connecting(tmp_path):
    library = MusicLibrary(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        build_session(object(), _settings(), library)
    message = str(caught.value)
    assert "腾讯云应用 ID" in message
    assert "大模型密钥" in message


@pytest.mark.asyncio
async def test_config_updates_voice_vad_and_llm_settings(tmp_path):
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import LLMUpdateSettingsFrame

    session = MusicSession(mode="client_url", library=MusicLibrary(tmp_path))

    class Analyzer:
        def __init__(self):
            self.params = VADParams(confidence=0.7, stop_secs=0.5, start_secs=0.2, min_volume=0.6)
            self.sample_rate = 0

    class Voice:
        def __init__(self):
            self.voice_type = 101001
            self.fast_voice_type = ""
            self.speed = 0.0
            self.volume = 0.0

    analyzer = Analyzer()
    voice = Voice()
    stage = MusicStage(session, vad_analyzer=analyzer, tts=voice, fallback_model="deepseek-chat")
    pushed = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    stage.push_frame = capture
    await stage.process_frame(
        ClientEventFrame(
            payload={
                "type": "config",
                "system_prompt": "只说一句。",
                "voice_type": 101004,
                "fast_voice_type": "fast-9",
                "speed": 1,
                "volume": -2,
                "vad_stop_secs": 0.8,
                "vad_confidence": 0.4,
                "llm_model": "demo-model",
                "music_mode": "server_mix",
            }
        ),
        FrameDirection.DOWNSTREAM,
    )
    assert session.mode == "server_mix"
    assert voice.voice_type == 101004
    assert voice.fast_voice_type == "fast-9"
    assert voice.speed == 1
    assert voice.volume == -2
    assert analyzer.params.stop_secs == 0.8
    assert analyzer.params.confidence == 0.4
    updates = [item for item in pushed if isinstance(item[0], LLMUpdateSettingsFrame)]
    assert len(updates) == 1
    frame, direction = updates[0]
    assert direction == FrameDirection.UPSTREAM
    assert frame.delta.system_instruction == "只说一句。"
    assert frame.delta.model == "demo-model"


@pytest.mark.asyncio
async def test_control_play_starts_first_track(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    session = MusicSession(mode="client_url", library=library)
    params = _Params()
    await session.control(params, "play", session.volume)
    assert session.playing is True
    assert session.session_mode == "music"
    assert session.current is not None
    assert params.result["ok"] is True


@pytest.mark.asyncio
async def test_mic_gate_drops_pcm_in_music_mode(tmp_path):
    session = MusicSession(mode="client_url", library=MusicLibrary(tmp_path))
    session.playing = True
    gate = ConversationMicGate(session)
    sent = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        sent.append(frame)

    gate.push_frame = capture
    audio = InputAudioRawFrame(audio=b"\x00\x01" * 8, sample_rate=16000, num_channels=1)
    await gate.process_frame(audio, FrameDirection.DOWNSTREAM)
    assert not any(isinstance(item, InputAudioRawFrame) for item in sent)
    assert any(isinstance(item, InterruptionFrame) for item in sent)
    assert any(getattr(item, "message", {}).get("type") == "session_mode" for item in sent)


@pytest.mark.asyncio
async def test_mic_gate_passes_pcm_in_conversation_mode(tmp_path):
    session = MusicSession(mode="client_url", library=MusicLibrary(tmp_path))
    gate = ConversationMicGate(session)
    sent = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        sent.append(frame)

    gate.push_frame = capture
    audio = InputAudioRawFrame(audio=b"\x00\x01" * 8, sample_rate=16000, num_channels=1)
    await gate.process_frame(audio, FrameDirection.DOWNSTREAM)
    assert sent == [audio]
