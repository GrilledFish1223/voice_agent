import wave
from itertools import islice

import numpy as np
import pytest

from voice_agent.processors.music_mixer import DUCK_GAIN, IDLE_GAIN, mix_int16
from voice_agent.services.music_library import MusicLibrary, read_pcm_frames


def test_seed_tracks_are_sixteen_khz_mono_wav(tmp_path):
    library = MusicLibrary(tmp_path)
    tracks = library.ensure_seed_tracks()
    assert [track.id for track in tracks] == ["morning_breeze", "deep_sea", "cyber_pulse"]
    assert [track.title for track in tracks] == ["晨光微风", "深海冥想", "赛博律动"]
    for track in tracks:
        assert track.artist == "LaunchKit Synth"
        assert track.url == f"/api/music/stream/{track.id}"
        with wave.open(str(track.wav_path), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == 16000
            assert handle.getsampwidth() == 2
            assert handle.getnframes() >= 16000 * 4
            payload = handle.readframes(handle.getnframes())
        assert payload != b"\x00" * len(payload)


def test_ensure_seed_tracks_skips_existing_files(tmp_path):
    library = MusicLibrary(tmp_path)
    tracks = library.ensure_seed_tracks()
    tracks[0].wav_path.write_bytes(b"already-present")
    library.ensure_seed_tracks()
    assert tracks[0].wav_path.read_bytes() == b"already-present"


def test_search_matches_title_before_genre(tmp_path):
    library = MusicLibrary(tmp_path)
    library.ensure_seed_tracks()
    found = library.search("深海", "")
    assert found.id == "deep_sea"
    assert library.search("", "电子").id == "cyber_pulse"
    assert library.search("morning_breeze", "电子").id == "morning_breeze"
    assert library.search("", "海").id == "deep_sea"
    assert library.search("", "冥想").id == "deep_sea"
    assert library.search("", "轻音乐").id == "morning_breeze"


def test_search_raises_when_library_is_empty(tmp_path):
    library = MusicLibrary(tmp_path)
    with pytest.raises(LookupError):
        library.search("深海", "电子")


def test_read_pcm_frames_skips_header_and_loops(tmp_path):
    library = MusicLibrary(tmp_path)
    track = library.ensure_seed_tracks()[0]
    raw = track.wav_path.read_bytes()
    assert raw[:4] == b"RIFF"
    pcm = raw[44:]
    assert len(pcm) == 16000 * 6 * 2
    frame_count = len(pcm) // 640
    frames = list(islice(read_pcm_frames(track, frame_bytes=640), frame_count + 1))
    assert frames[0] == pcm[:640]
    assert all(len(frame) == 640 for frame in frames)
    assert frames[frame_count] == frames[0]


def test_mix_int16_ducks_music_and_clips():
    # 10000 + int(10000 * 0.18) 仍在 int16 内。
    # 30000 + int(30000 * 0.18) 会超过 32767，先被削顶，不能用来比较闪避和空闲音量。
    speech = np.array([10000, -10000], dtype=np.int16).tobytes()
    bed = np.array([10000, 10000], dtype=np.int16).tobytes()
    ducked = np.frombuffer(mix_int16(speech, bed, DUCK_GAIN), dtype=np.int16)
    idle = np.frombuffer(mix_int16(speech, bed, IDLE_GAIN), dtype=np.int16)
    assert int(ducked[0]) == 10000 + int(10000 * DUCK_GAIN)
    assert ducked[0] < idle[0]
    saturated = mix_int16(
        np.array([32767], dtype=np.int16).tobytes(),
        np.array([32767], dtype=np.int16).tobytes(),
        1.0,
    )
    assert int(np.frombuffer(saturated, dtype=np.int16)[0]) == 32767
