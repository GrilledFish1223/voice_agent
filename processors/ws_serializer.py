from __future__ import annotations

import json
from dataclasses import dataclass

from pipecat.frames.frames import (
    DataFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

try:
    from pipecat.frames.frames import StartInterruptionFrame
except ImportError:  # Pipecat 1.x 后只保留 InterruptionFrame
    StartInterruptionFrame = None  # type: ignore[misc,assignment]

try:
    from pipecat.frames.frames import TTSAudioRawFrame
except ImportError:
    TTSAudioRawFrame = OutputAudioRawFrame  # type: ignore[misc,assignment]


@dataclass
class ClientEventFrame(DataFrame):
    payload: dict


def client_message(payload: dict) -> OutputTransportMessageUrgentFrame:
    """下行 JSON 必须是传输消息，输出端才会调用序列化器。"""
    return OutputTransportMessageUrgentFrame(message=payload)


class PcmJsonFrameSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame, InputAudioRawFrame)):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message, ensure_ascii=False)
        if isinstance(frame, ClientEventFrame):
            return json.dumps(frame.payload, ensure_ascii=False)
        interruption = tuple(
            item for item in (InterruptionFrame, StartInterruptionFrame) if isinstance(item, type)
        )
        if interruption and isinstance(frame, interruption):
            return json.dumps({"type": "clear_audio"}, ensure_ascii=False)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)):
            return InputAudioRawFrame(audio=bytes(data), sample_rate=16000, num_channels=1)
        payload = json.loads(data)
        if not isinstance(payload, dict) or "type" not in payload:
            return None
        return ClientEventFrame(payload=payload)
