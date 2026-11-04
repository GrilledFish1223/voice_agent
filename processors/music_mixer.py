from __future__ import annotations

import numpy as np

DUCK_GAIN = 0.18
IDLE_GAIN = 0.7


def mix_int16(tts: bytes, music: bytes, music_gain: float) -> bytes:
    speech = np.frombuffer(tts, dtype=np.int16).astype(np.int32)
    bed = np.frombuffer(music, dtype=np.int16).astype(np.int32)
    size = min(speech.size, bed.size)
    mixed = speech[:size] + np.round(bed[:size] * music_gain).astype(np.int32)
    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes()
