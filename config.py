from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_PROMPT = (
    "你是一个专业热情的智能语音助手，可以陪聊和播放音乐。"
    "回答使用口语，每次不超过两句话。用户要求放歌、暂停、切歌或调节音量时，调用音乐工具。"
)


@dataclass
class SessionConfig:
    system_prompt: str = DEFAULT_PROMPT
    voice_type: int = 101001
    fast_voice_type: str = ""
    speed: float = 0.0
    volume: float = 0.0
    vad_stop_secs: float = 0.5
    vad_confidence: float = 0.7
    llm_model: str = ""
    music_mode: str = "client_url"

    def __post_init__(self) -> None:
        if self.music_mode not in ("client_url", "server_mix"):
            raise ValueError("music_mode 只能是 client_url 或 server_mix")
        self.voice_type = int(self.voice_type)
        self.speed = float(self.speed)
        self.volume = float(self.volume)
        self.vad_stop_secs = min(2.0, max(0.2, float(self.vad_stop_secs)))
        self.vad_confidence = min(1.0, max(0.3, float(self.vad_confidence)))


@dataclass(frozen=True)
class Settings:
    tencent_app_id: str
    tencent_secret_id: str
    tencent_secret_key: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    host: str
    port: int

    def missing_required(self) -> list[str]:
        mapping = {
            "TENCENT_APP_ID": self.tencent_app_id,
            "TENCENT_SECRET_ID": self.tencent_secret_id,
            "TENCENT_SECRET_KEY": self.tencent_secret_key,
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_API_KEY": self.llm_api_key,
            "LLM_MODEL": self.llm_model,
        }
        return [name for name, value in mapping.items() if not value]


def load_settings() -> Settings:
    return Settings(
        tencent_app_id=os.getenv("TENCENT_APP_ID", ""),
        tencent_secret_id=os.getenv("TENCENT_SECRET_ID", ""),
        tencent_secret_key=os.getenv("TENCENT_SECRET_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        host=os.getenv("VOICE_AGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("VOICE_AGENT_PORT", "8765")),
    )
