from __future__ import annotations

import asyncio
import contextlib
import time

import websockets
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from voice_agent.config import SessionConfig, Settings
from voice_agent.processors.caption_tap import CaptionTap
from voice_agent.processors.music_mixer import DUCK_GAIN, IDLE_GAIN, mix_int16
from voice_agent.processors.tencent_asr import TencentASRService
from voice_agent.processors.tencent_tts import TencentTTSService
from voice_agent.processors.ws_serializer import ClientEventFrame, PcmJsonFrameSerializer
from voice_agent.services.music_library import MusicLibrary, MusicTrack, read_pcm_frames

try:
    from pipecat.frames.frames import LLMUpdateSettingsFrame
except ImportError:  # 旧版本没有运行时设置帧
    LLMUpdateSettingsFrame = None  # type: ignore[misc,assignment]

_MISSING_NAMES = {
    "TENCENT_APP_ID": "腾讯云应用 ID",
    "TENCENT_SECRET_ID": "腾讯云密钥 ID",
    "TENCENT_SECRET_KEY": "腾讯云密钥",
    "LLM_BASE_URL": "大模型接口地址",
    "LLM_API_KEY": "大模型密钥",
    "LLM_MODEL": "大模型名称",
}
_CONFIG_FIELDS = (
    "system_prompt",
    "voice_type",
    "fast_voice_type",
    "speed",
    "volume",
    "vad_stop_secs",
    "vad_confidence",
    "llm_model",
    "music_mode",
)
_VOLUME_STEP = 0.1
_FRAME_BYTES = 640


def _require_settings(settings: Settings) -> None:
    missing = settings.missing_required()
    if not missing:
        return
    names = "、".join(_MISSING_NAMES.get(item, item) for item in missing)
    raise RuntimeError(f"缺少必要配置：{names}")


def _clamp_volume(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _track_payload(track: MusicTrack) -> dict:
    return {
        "id": track.id,
        "title": track.title,
        "artist": track.artist,
        "url": track.url,
    }


class _CommandParams:
    async def result_callback(self, data: dict) -> None:
        return None


class MusicSession:
    """一次连接里的播放状态。工具函数只是把它交给模型。"""

    def __init__(self, mode: str, library: MusicLibrary) -> None:
        self.mode = mode
        self.library = library
        self.current: MusicTrack | None = None
        self.playing = False
        self.speech_active = False
        self.volume = 0.8
        self.outbound: asyncio.Queue[dict] = asyncio.Queue()

    async def play(self, params, query: str, genre: str) -> None:
        try:
            track = self.library.search(query or "", genre or "")
        except LookupError as exc:
            await params.result_callback({"ok": False, "error": str(exc)})
            return
        self.current = track
        self.playing = True
        if self.mode == "client_url":
            await self.outbound.put(
                {
                    "type": "music_control",
                    "action": "play",
                    "track": _track_payload(track),
                    "volume": self.volume,
                    "ducking": True,
                }
            )
        await params.result_callback({"ok": True, "title": track.title, "mode": self.mode})

    async def control(self, params, action: str, volume: float) -> None:
        action = str(action or "").strip()
        try:
            requested = float(volume)
        except (TypeError, ValueError):
            requested = self.volume
        announced = self.current
        if action == "pause":
            self.playing = False
        elif action == "resume":
            self.playing = True
        elif action == "stop":
            self.playing = False
            self.current = None
        elif action == "next":
            track = self._cycle_next()
            if track is None:
                await params.result_callback({"ok": False, "error": "曲库里没有曲目"})
                return
            self.current = track
            announced = track
            self.playing = True
        elif action == "volume_up":
            if requested > self.volume:
                self.volume = _clamp_volume(requested)
            else:
                self.volume = _clamp_volume(self.volume + _VOLUME_STEP)
        elif action == "volume_down":
            if requested < self.volume:
                self.volume = _clamp_volume(requested)
            else:
                self.volume = _clamp_volume(self.volume - _VOLUME_STEP)
        else:
            await params.result_callback({"ok": False, "error": f"不支持的音乐动作：{action}"})
            return
        if self.mode == "client_url":
            await self.outbound.put(self._control_payload(action, announced))
        title = self.current.title if self.current is not None else ""
        await params.result_callback(
            {
                "ok": True,
                "action": action,
                "title": title,
                "volume": self.volume,
                "playing": self.playing,
                "mode": self.mode,
            }
        )

    def _cycle_next(self) -> MusicTrack | None:
        tracks = self.library.ensure_seed_tracks()
        if not tracks:
            return None
        if self.current is None:
            return tracks[0]
        ids = [track.id for track in tracks]
        try:
            index = ids.index(self.current.id)
        except ValueError:
            return tracks[0]
        return tracks[(index + 1) % len(tracks)]

    def _control_payload(self, action: str, track: MusicTrack | None) -> dict:
        payload = {
            "type": "music_control",
            "action": action,
            "volume": self.volume,
            "ducking": True,
        }
        if track is not None:
            payload["track"] = _track_payload(track)
        return payload


class MusicStage(FrameProcessor):
    """TTS 之后的音乐层：客户端拉流只转发控制消息，服务端混音则叠到语音上。"""

    def __init__(
        self,
        session: MusicSession,
        *,
        vad_analyzer=None,
        tts=None,
        fallback_model: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = session
        self._vad = vad_analyzer
        self._tts = tts
        self._fallback_model = fallback_model
        mode = session.mode if session.mode in ("client_url", "server_mix") else "client_url"
        self._config = SessionConfig(music_mode=mode)
        self._music_track_id: str | None = None
        self._music_iter = None
        self._pending = b""
        self._last_tts_at = 0.0
        self._pump_task: asyncio.Task | None = None
        self._closed = False

    def mix_tts_chunk(self, tts_pcm: bytes) -> bytes:
        if (
            self.session.mode != "server_mix"
            or not self.session.playing
            or self.session.current is None
            or not tts_pcm
        ):
            return tts_pcm
        music = self._take_music(len(tts_pcm))
        gain = DUCK_GAIN if self.session.speech_active else IDLE_GAIN
        return mix_int16(tts_pcm, music, gain)

    def _take_music(self, size: int) -> bytes:
        track = self.session.current
        if track is None or size <= 0:
            return b"\x00" * max(size, 0)
        if self._music_iter is None or self._music_track_id != track.id:
            self._music_track_id = track.id
            self._music_iter = read_pcm_frames(track, frame_bytes=_FRAME_BYTES)
            self._pending = b""
        while len(self._pending) < size:
            self._pending += next(self._music_iter)
        chunk = self._pending[:size]
        self._pending = self._pending[size:]
        return chunk

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if self._task_manager is not None:
            await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, StartFrame):
            self._ensure_pump()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop_pump()
        await self._flush_outbound()
        if isinstance(frame, ClientEventFrame):
            await self._on_client_event(frame)
            return
        if isinstance(frame, OutputAudioRawFrame) and frame.audio:
            frame.audio = self.mix_tts_chunk(bytes(frame.audio))
            channels = int(getattr(frame, "num_channels", 1) or 1)
            frame.num_frames = len(frame.audio) // (2 * channels)
            self._last_tts_at = time.monotonic()
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await self._stop_pump()
        await super().cleanup()

    async def _on_client_event(self, frame: ClientEventFrame) -> None:
        payload = frame.payload or {}
        kind = payload.get("type")
        if kind == "config":
            await self._apply_config(payload)
            return
        if kind == "interrupt":
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            return
        if kind == "music_cmd":
            action = str(payload.get("action") or "")
            raw_volume = payload.get("volume", self.session.volume)
            try:
                volume = float(raw_volume)
            except (TypeError, ValueError):
                volume = self.session.volume
            await self.session.control(_CommandParams(), action, volume)
            await self._flush_outbound()
            return
        await self.push_frame(frame)

    async def _apply_config(self, payload: dict) -> None:
        data = {name: getattr(self._config, name) for name in _CONFIG_FIELDS}
        for name in _CONFIG_FIELDS:
            if name in payload and payload[name] is not None:
                data[name] = payload[name]
        try:
            updated = SessionConfig(**data)
        except ValueError as exc:
            await self.push_frame(ClientEventFrame(payload={"type": "error", "message": str(exc)}))
            return
        self._config = updated
        self.session.mode = updated.music_mode
        self._write_voice(updated)
        self._write_vad(updated)
        await self._push_llm_settings(updated)

    def _write_voice(self, config: SessionConfig) -> None:
        tts = self._tts
        if tts is None:
            return
        tts.voice_type = config.voice_type
        tts.fast_voice_type = config.fast_voice_type
        tts.speed = config.speed
        tts.volume = config.volume

    def _write_vad(self, config: SessionConfig) -> None:
        analyzer = self._vad
        if analyzer is None or not hasattr(analyzer, "params"):
            return
        current = analyzer.params
        start_secs = getattr(current, "start_secs", 0.2)
        min_volume = getattr(current, "min_volume", 0.6)
        try:
            current.stop_secs = config.vad_stop_secs
            current.confidence = config.vad_confidence
            updated = current
        except Exception:
            updated = VADParams(
                confidence=config.vad_confidence,
                stop_secs=config.vad_stop_secs,
                start_secs=start_secs,
                min_volume=min_volume,
            )
            if hasattr(analyzer, "set_params"):
                self._commit_vad(analyzer, updated)
                return
            try:
                analyzer.params = updated
            except Exception:
                analyzer._params = updated
            return
        self._commit_vad(analyzer, updated)

    def _commit_vad(self, analyzer, params: VADParams) -> None:
        # stop_secs 改在模型上不会重算静音帧数，采样率就绪后要再 set_params。
        rate = getattr(analyzer, "sample_rate", 0) or 0
        if rate > 0 and hasattr(analyzer, "set_params"):
            analyzer.set_params(params)

    async def _push_llm_settings(self, config: SessionConfig) -> None:
        if not isinstance(LLMUpdateSettingsFrame, type):
            return
        model = config.llm_model or self._fallback_model
        delta = OpenAILLMService.Settings(model=model, system_instruction=config.system_prompt)
        await self.push_frame(
            LLMUpdateSettingsFrame(delta=delta),
            FrameDirection.UPSTREAM,
        )

    async def _flush_outbound(self) -> None:
        if self.session.mode != "client_url":
            return
        while True:
            try:
                payload = self.session.outbound.get_nowait()
            except asyncio.QueueEmpty:
                return
            await self.push_frame(ClientEventFrame(payload=payload))

    def _ensure_pump(self) -> None:
        if self._pump_task is not None and not self._pump_task.done():
            return
        self._closed = False
        self._pump_task = asyncio.create_task(self._pump_idle())

    async def _stop_pump(self) -> None:
        self._closed = True
        task = self._pump_task
        self._pump_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _pump_idle(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(0.02)
                playing = (
                    self.session.mode == "server_mix"
                    and self.session.playing
                    and self.session.current is not None
                )
                if not playing:
                    continue
                if time.monotonic() - self._last_tts_at < 0.25:
                    continue
                mixed = self.mix_tts_chunk(b"\x00" * _FRAME_BYTES)
                await self.push_frame(
                    OutputAudioRawFrame(audio=mixed, sample_rate=16000, num_channels=1)
                )
        except asyncio.CancelledError:
            return


def _relay_captions(processor: FrameProcessor, tap: CaptionTap) -> None:
    """识别和回复在字幕处理器后面产生，挂到 push_frame 上才能发给客户端。"""
    original = processor.push_frame

    async def push_frame(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        payload = tap.claim(frame)
        await original(frame, direction)
        if payload is not None:
            await original(ClientEventFrame(payload=payload), direction)

    processor.push_frame = push_frame  # type: ignore[method-assign]


def build_session(websocket, settings: Settings, library: MusicLibrary) -> PipelineTask:
    """组装一条还没启动的会话管道。缺密钥时不连接腾讯云，也不创建模型客户端。"""
    _require_settings(settings)
    config = SessionConfig()
    analyzer = SileroVADAnalyzer(
        params=VADParams(
            confidence=config.vad_confidence,
            stop_secs=config.vad_stop_secs,
            start_secs=0.2,
            min_volume=0.6,
        )
    )
    vad = VADProcessor(vad_analyzer=analyzer)
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            add_wav_header=False,
            serializer=PcmJsonFrameSerializer(),
            fixed_audio_packet_size=640,
        ),
    )
    session = MusicSession(mode=config.music_mode, library=library)
    asr = TencentASRService(
        app_id=settings.tencent_app_id,
        secret_id=settings.tencent_secret_id,
        secret_key=settings.tencent_secret_key,
        connect=websockets.connect,
    )
    tts = TencentTTSService(
        app_id=settings.tencent_app_id,
        secret_id=settings.tencent_secret_id,
        secret_key=settings.tencent_secret_key,
        voice_type=config.voice_type,
        fast_voice_type=config.fast_voice_type,
        speed=config.speed,
        volume=config.volume,
        connect=websockets.connect,
    )
    model_name = config.llm_model or settings.llm_model
    llm = OpenAILLMService(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        settings=OpenAILLMService.Settings(
            model=model_name,
            system_instruction=config.system_prompt,
        ),
    )

    async def play_music(params: FunctionCallParams, query: str, genre: str):
        """播放曲库中的音乐。

        Args:
            query: 歌名、歌手或用户原话中的关键词。
            genre: 风格。电子、轻音乐、环境音都可以；没有明确风格时传空字符串。
        """
        await session.play(params, query, genre)

    async def control_music(params: FunctionCallParams, action: str, volume: float):
        """控制当前音乐。

        Args:
            action: pause、resume、stop、next、volume_up、volume_down 之一。
            volume: 0 到 1。action 不是音量调节时传当前音量。
        """
        await session.control(params, action, volume)

    context = LLMContext(tools=[play_music, control_music])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
    caption_tap = CaptionTap(session)
    music_stage = MusicStage(
        session,
        vad_analyzer=analyzer,
        tts=tts,
        fallback_model=settings.llm_model,
    )
    _relay_captions(asr, caption_tap)
    _relay_captions(llm, caption_tap)
    _relay_captions(tts, caption_tap)
    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            caption_tap,
            asr,
            user_aggregator,
            llm,
            tts,
            music_stage,
            transport.output(),
            assistant_aggregator,
        ]
    )
    # 1.15 默认会在管道前塞 RTVI，和这条自定义 JSON 协议冲突。
    return PipelineTask(pipeline, enable_rtvi=False)
