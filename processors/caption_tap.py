from __future__ import annotations

from pipecat.frames import frames as pipecat_frames
from pipecat.frames.frames import Frame, InterruptionFrame, MetricsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_agent.processors.ws_serializer import ClientEventFrame, client_message


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


def metrics_payload(frame: Frame) -> dict | None:
    """把 Pipecat MetricsFrame 收成页面能画的延迟事件。"""
    if not isinstance(frame, MetricsFrame):
        return None
    items = []
    for data in getattr(frame, "data", None) or []:
        kind = type(data).__name__.replace("MetricsData", "")
        processor = str(getattr(data, "processor", "") or "").split("#", 1)[0]
        item = {"kind": kind, "processor": processor}
        value = getattr(data, "value", None)
        if isinstance(value, (int, float)):
            item["value"] = round(float(value), 3)
        for key in ("ttfb", "ttfa", "ttfat", "thinking_time", "leading_silence"):
            number = getattr(data, key, None)
            if isinstance(number, (int, float)):
                item[key] = round(float(number), 3)
        items.append(item)
    if not items:
        return None
    return {"type": "metrics", "items": items}


def latency_payload(seconds: float, parts: list[dict] | None = None) -> dict:
    """用户停说到机器人开说的端到端延迟。"""
    payload = {"type": "metrics", "user_to_bot_secs": round(float(seconds), 3)}
    if parts:
        payload["parts"] = parts
    return payload


def caption_payload(frame: Frame) -> dict | None:
    """对齐官网网页示例：interim 只覆盖当前句，TranscriptionFrame 才落一条用户字幕。"""
    metrics = metrics_payload(frame)
    if metrics is not None:
        return metrics
    text = getattr(frame, "text", "") or ""
    if _matches(frame, "InterimTranscriptionFrame"):
        return {"type": "user_transcript", "text": text, "final": False}
    if _matches(frame, "TranscriptionFrame"):
        return {"type": "user_transcript", "text": text, "final": True}
    if _matches(frame, "LLMTextFrame"):
        return {"type": "bot_output", "text": text, "final": False}
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
            await self.push_frame(client_message(payload), direction)

    async def publish(self, payload: dict) -> None:
        """轮次字幕、端到端延迟这类不挂在某一帧上的事件，直接推给客户端。"""
        if payload:
            await self.push_frame(client_message(payload))
