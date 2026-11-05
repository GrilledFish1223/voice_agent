import json
from pathlib import Path


def test_miniprogram_uses_shared_websocket_protocol():
    root = Path("voice_agent/miniprogram")
    page = (root / "pages/voip/index.js").read_text(encoding="utf-8")
    player = (root / "pages/voip/pcm-player.js").read_text(encoding="utf-8")
    config = json.loads((root / "app.json").read_text(encoding="utf-8"))
    assert config["pages"] == ["pages/voip/index"]
    assert 'format: "pcm"' in page
    assert "sampleRate: 16000" in page
    assert "clear_audio" in page
    assert "music_control" in page
    hangup = page[page.index("endCall(updateStatus)") :]
    assert "this.releaseMusicAudio();" in hangup
    assert 'this.data.sessionMode === "music"' in page
    assert 'type === "session_mode"' in page
    assert "createWebAudioContext" in player
    assert "nextStartTime" in player
