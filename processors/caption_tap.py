from __future__ import annotations

from pipecat.frames import frames as pipecat_frames
from pipecat.frames.frames import Frame, InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_agent.processors.ws_serializer import ClientEventFrame


def _frame_type(name: str):
    candidate = getattr(pipecat_frames, name, None)
    if isinstance(candidate, type):
        return candidate
    return None


def _matches(frame: Frame, *names: str) -> bool:
    for name in names:
        kind = _frame_type(name)
        if kind is not None and isinstance(frame, kind):
            return True
    return False


def caption_payload(frame: Frame) -> dict | None:
    """把经过管道的一帧收成客户端字幕事件。缺类名时直接跳过。"""
    text = getattr(frame, "text", "") or ""
    if _matches(frame, "InterimTranscriptionFrame"):
        return {"type": "asr_interim", "text": text}
    if _matches(frame, "TranscriptionFrame"):
        return {"type": "asr_final", "text": text}
    if _matches(frame, "LLMTextFrame"):
        return {"type": "llm_delta", "text": text}
    if _matches(frame, "LLMFullResponseEndFrame"):
        return {"type": "llm_done"}
    if _matches(frame, "UserStartedSpeakingFrame", "VADUserStartedSpeakingFrame"):
        return {"type": "user_speaking", "active": True}
    if _matches(frame, "UserStoppedSpeakingFrame", "VADUserStoppedSpeakingFrame"):
        return {"type": "user_speaking", "active": False}
    if _matches(frame, "TTSStartedFrame"):
        return {"type": "bot_speaking", "active": True}
    if _matches(frame, "TTSStoppedFrame"):
        return {"type": "bot_speaking", "active": False}
    return None


class CaptionTap(FrameProcessor):
    """原帧继续往下走，同时多推一条客户端字幕或说话状态。"""

    def __init__(self, session=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._seen: set[int] = set()

    def claim(self, frame: Frame) -> dict | None:
        payload = caption_payload(frame)
        if payload is None:
            return None
        marker = getattr(frame, "id", id(frame))
        if marker in self._seen:
            return None
        self._seen.add(marker)
        if self._session is not None and payload.get("type") == "user_speaking":
            self._session.speech_active = bool(payload.get("active"))
        return payload

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # StartFrame 会让基类创建处理任务。Pipeline 先注入 TaskManager；单独调用时跳过。
        if self._task_manager is not None:
            await super().process_frame(frame, direction)
        payload = self.claim(frame)
        # 打断要在合成之前变成下游 InterruptionFrame，腾讯 TTS 只处理下游打断。
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, ClientEventFrame)
            and frame.payload.get("type") == "interrupt"
        ):
            await self.push_frame(InterruptionFrame(), direction)
        await self.push_frame(frame, direction)
        if payload is not None:
            await self.push_frame(ClientEventFrame(payload=payload), direction)
