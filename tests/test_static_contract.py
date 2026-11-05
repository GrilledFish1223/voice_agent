from pathlib import Path


def test_web_client_contains_protocol_and_ducking():
    root = Path(__file__).resolve().parents[1] / "static"
    script = (root / "app.js").read_text(encoding="utf-8")
    worklet = (root / "audio-worklet.js").read_text(encoding="utf-8")
    assert "clear_audio" in script
    assert "music_control" in script
    hangup = script[script.index("async function releaseCallMedia") :]
    assert "releaseMusicAudio();" in hangup
    assert 'if (sessionMode === "music")' in script
    assert 'type === "session_mode"' in script
    assert "echoCancellation: true" in script
    assert "0.2" in script
    assert "16000" in worklet
