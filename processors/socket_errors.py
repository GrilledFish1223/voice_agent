from __future__ import annotations


def is_abrupt_close(error: object) -> bool:
    """腾讯云鉴权失败时经常不发 WebSocket 关闭帧，库会抛出这句英文。"""
    return "no close frame received or sent" in str(error)
