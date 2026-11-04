from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

SAMPLE_RATE = 16_000
DURATION_SECONDS = 6
AMPLITUDE = 0.2
_ARTIST = "LaunchKit Synth"


@dataclass(frozen=True)
class MusicTrack:
    id: str
    title: str
    artist: str
    url: str
    wav_path: Path


@dataclass(frozen=True)
class _SeedTrack:
    id: str
    title: str
    artist: str
    frequencies: tuple[float, ...]
    pulse: bool = False


_SEEDS: tuple[_SeedTrack, ...] = (
    _SeedTrack("morning_breeze", "晨光微风", _ARTIST, (261.63, 329.63, 392.00)),
    _SeedTrack("deep_sea", "深海冥想", _ARTIST, (146.83, 220.00, 293.66)),
    _SeedTrack("cyber_pulse", "赛博律动", _ARTIST, (440.0,), pulse=True),
)


class MusicLibrary:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def ensure_seed_tracks(self) -> list[MusicTrack]:
        self.directory.mkdir(parents=True, exist_ok=True)
        for seed in _SEEDS:
            path = self._wav_path(seed.id)
            if path.is_file():
                continue
            _write_wav(path, _render(seed))
        return self._existing_tracks()

    def search(self, query: str, genre: str) -> MusicTrack:
        tracks = self._existing_tracks()
        if not tracks:
            raise LookupError("曲库里没有曲目")
        needle = query.strip()
        if needle:
            for track in tracks:
                if needle in track.title or needle in track.artist or needle in track.id:
                    return track
        chosen_id = _genre_track_id(genre)
        for track in tracks:
            if track.id == chosen_id:
                return track
        return tracks[0]

    def _wav_path(self, track_id: str) -> Path:
        return self.directory / f"{track_id}.wav"

    def _existing_tracks(self) -> list[MusicTrack]:
        tracks: list[MusicTrack] = []
        for seed in _SEEDS:
            path = self._wav_path(seed.id)
            if path.is_file():
                tracks.append(_to_track(seed, path))
        return tracks


def read_pcm_frames(track: MusicTrack, frame_bytes: int = 640) -> Iterator[bytes]:
    pcm = _pcm_payload(Path(track.wav_path).read_bytes())
    if frame_bytes <= 0 or frame_bytes % 2:
        raise ValueError("frame_bytes 必须是正偶数")
    if not pcm:
        return
    offset = 0
    total = len(pcm)
    while True:
        frame = bytearray()
        while len(frame) < frame_bytes:
            if offset >= total:
                offset = 0
            take = min(frame_bytes - len(frame), total - offset)
            frame.extend(pcm[offset : offset + take])
            offset += take
        yield bytes(frame)


def _to_track(seed: _SeedTrack, path: Path) -> MusicTrack:
    return MusicTrack(
        id=seed.id,
        title=seed.title,
        artist=seed.artist,
        url=f"/api/music/stream/{seed.id}",
        wav_path=path,
    )


def _genre_track_id(genre: str) -> str:
    if "电子" in genre:
        return "cyber_pulse"
    if "海" in genre or "冥想" in genre:
        return "deep_sea"
    return "morning_breeze"


def _render(seed: _SeedTrack) -> np.ndarray:
    count = SAMPLE_RATE * DURATION_SECONDS
    timeline = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    if seed.pulse:
        mixed = _pulse(timeline)
    else:
        mixed = _chord(seed.frequencies, timeline)
    return np.round(np.clip(mixed, -1.0, 1.0) * 32767.0).astype("<i2")


def _chord(frequencies: tuple[float, ...], timeline: np.ndarray) -> np.ndarray:
    mixed = np.zeros_like(timeline)
    for frequency in frequencies:
        mixed += np.sin(2.0 * np.pi * frequency * timeline)
    return mixed * (AMPLITUDE / len(frequencies))


def _pulse(timeline: np.ndarray) -> np.ndarray:
    mixed = AMPLITUDE * np.sin(2.0 * np.pi * 440.0 * timeline)
    pulse_len = max(1, int(0.02 * SAMPLE_RATE))
    interval = int(0.5 * SAMPLE_RATE)
    for start in range(0, timeline.size, interval):
        end = min(timeline.size, start + pulse_len)
        window = np.linspace(1.0, 0.0, end - start, endpoint=False)
        mixed[start:end] += AMPLITUDE * window * np.sin(
            2.0 * np.pi * 880.0 * timeline[start:end]
        )
    return mixed


def _write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.astype("<i2", copy=False).tobytes())


def _pcm_payload(blob: bytes) -> bytes:
    if not _is_standard_pcm_wav(blob):
        return blob[: len(blob) - (len(blob) % 2)]
    data_size = int.from_bytes(blob[40:44], "little")
    payload = blob[44 : 44 + data_size]
    if len(payload) % 2:
        payload = payload[:-1]
    return payload


def _is_standard_pcm_wav(blob: bytes) -> bool:
    if len(blob) < 44:
        return False
    if blob[0:4] != b"RIFF" or blob[8:12] != b"WAVE" or blob[12:16] != b"fmt ":
        return False
    fmt_size = int.from_bytes(blob[16:20], "little")
    audio_format = int.from_bytes(blob[20:22], "little")
    return fmt_size == 16 and audio_format == 1 and blob[36:40] == b"data"
