import json

import pytest
from pipecat.frames import frames as pipecat_frames
from pipecat.frames.frames import OutputTransportMessageUrgentFrame

from voice_agent.processors.ws_serializer import ClientEventFrame, PcmJsonFrameSerializer


@pytest.mark.asyncio
async def test_binary_audio_roundtrip_uses_sixteen_khz_mono():
    serializer = PcmJsonFrameSerializer()
    pcm = b"\x01\x00\xff\xff"
    frame = await serializer.deserialize(pcm)
    assert frame.audio == pcm
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1
    assert await serializer.serialize(frame) == pcm


@pytest.mark.asyncio
async def test_config_json_becomes_client_event():
    serializer = PcmJsonFrameSerializer()
    frame = await serializer.deserialize('{"type":"config","music_mode":"server_mix"}')
    assert isinstance(frame, ClientEventFrame)
    assert frame.payload["music_mode"] == "server_mix"


@pytest.mark.asyncio
async def test_client_event_serializes_to_json_text():
    serializer = PcmJsonFrameSerializer()
    encoded = await serializer.serialize(
        ClientEventFrame(payload={"type": "music_control", "action": "play"})
    )
    assert json.loads(encoded)["action"] == "play"


@pytest.mark.asyncio
async def test_output_transport_message_serializes_music_control():
    serializer = PcmJsonFrameSerializer()
    encoded = await serializer.serialize(
        OutputTransportMessageUrgentFrame(message={"type": "music_control", "action": "play"})
    )
    assert json.loads(encoded)["action"] == "play"


@pytest.mark.asyncio
async def test_interruption_serializes_to_clear_audio():
    start_cls = getattr(pipecat_frames, "StartInterruptionFrame", None)
    interrupt_cls = getattr(pipecat_frames, "InterruptionFrame", None)
    frames = [cls() for cls in (start_cls, interrupt_cls) if isinstance(cls, type)]
    assert frames, "at least one interruption frame class must exist"

    serializer = PcmJsonFrameSerializer()
    saw_clear = False
    for frame in frames:
        encoded = await serializer.serialize(frame)
        if encoded is None:
            continue
        assert json.loads(encoded)["type"] == "clear_audio"
        saw_clear = True
    assert saw_clear, "at least one interruption class must serialize to clear_audio"
