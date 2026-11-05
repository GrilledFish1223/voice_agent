from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_PROMPT = (
    "你是口语语音助手，可以陪聊和播放音乐。"
    "直接开口，每次最多两句短话，不要内心独白。"
    "只有用户明确说放歌、暂停、切歌或调音量时才调用音乐工具。"
    "开始放歌后会进入音乐模式并暂停拾音，用户点暂停或停止后再继续对话。"
    "用户说太慢、没声音属于系统反馈，不要当放歌指令。"
)


@dataclass
class SessionConfig:
    system_prompt: str = DEFAULT_PROMPT
    voice_type: int = 602003
    fast_voice_type: str = ""
    speed: float = 0.0
    volume: float = 0.0
    vad_stop_secs: float = 0.2
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


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip().strip('"')
        if value:
            return value
    return ""


def load_settings() -> Settings:
    return Settings(
        tencent_app_id=_env("TENCENT_CLOUD_APP_ID", "TENCENT_APP_ID"),
        tencent_secret_id=_env("TENCENT_CLOUD_SECRET_ID", "TENCENT_SECRET_ID"),
        tencent_secret_key=_env("TENCENT_CLOUD_SECRET_KEY", "TENCENT_SECRET_KEY"),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        host=os.getenv("VOICE_AGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("VOICE_AGENT_PORT", "8765")),
    )
