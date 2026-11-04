import pytest
from pipecat.frames.frames import LLMTextFrame, TranscriptionFrame, VADUserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from voice_agent.config import Settings
from voice_agent.pipeline import MusicSession, MusicStage, build_session
from voice_agent.processors.caption_tap import CaptionTap
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
    message = session.outbound.get_nowait()
    assert message["type"] == "music_control"
    assert message["track"]["id"] == "deep_sea"
    assert params.result["title"] == "深海冥想"


@pytest.mark.asyncio
async def test_server_mix_play_does_not_enqueue_url(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    session = MusicSession(mode="server_mix", library=library)
    params = _Params()
    await session.play(params, "深海", "")
    assert session.outbound.empty()
    assert session.playing is True
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
    assert sent[1].message == {"type": "asr_final", "text": "你好"}

    sent.clear()
    await tap.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert session.speech_active is True
    assert sent[-1].message == {"type": "user_speaking", "active": True}

    sent.clear()
    await tap.process_frame(LLMTextFrame("好的"), FrameDirection.DOWNSTREAM)
    assert sent[-1].message == {"type": "llm_delta", "text": "好的"}


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
