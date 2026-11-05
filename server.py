from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.websocket.fastapi import FastAPIWebsocketInputTransport

from voice_agent.config import Settings, load_settings
from voice_agent.pipeline import build_session
from voice_agent.services.music_library import MusicLibrary, MusicTrack
from voice_agent.services.voice_catalog import clone_voice, list_voices

_PACKAGE_DIR = Path(__file__).resolve().parent
_ROOT_ENV = _PACKAGE_DIR.parent / ".env"
_STATIC_DIR = _PACKAGE_DIR / "static"
_INDEX_FILE = _STATIC_DIR / "index.html"
_MUSIC_DIR = _PACKAGE_DIR / "var" / "music"
_PENDING_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>voice-agent</title></head>
<body><p>通话页还没有做好。</p></body>
</html>
"""


def create_app(
    settings: Settings | None = None,
    library: MusicLibrary | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else load_settings()
    music = library if library is not None else MusicLibrary(_MUSIC_DIR)
    music.ensure_seed_tracks()

    app = FastAPI(title="voice-agent")

    @app.get("/", response_model=None)
    def index() -> FileResponse | HTMLResponse:
        if _INDEX_FILE.is_file():
            return FileResponse(_INDEX_FILE)
        return HTMLResponse(_PENDING_PAGE)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"ok": True, "missing": resolved.missing_required()}

    @app.get("/api/voices")
    def voices() -> list[dict[str, object]]:
        return list_voices()

    @app.get("/api/music")
    def music_tracks() -> list[dict[str, str]]:
        return [_public_track(track) for track in music.ensure_seed_tracks()]

    @app.get("/api/music/stream/{track_id}")
    def stream_track(track_id: str) -> FileResponse:
        track = _find_track(music, track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="曲目不存在")
        return FileResponse(
            track.wav_path,
            media_type="audio/wav",
            filename=f"{track.id}.wav",
            content_disposition_type="inline",
        )

    @app.post("/api/clone-voice")
    async def clone_voice_route(
        audio: UploadFile = File(...),
        text: str = Form(...),
        voice_name: str = Form(...),
    ) -> dict[str, str]:
        payload = await audio.read()
        try:
            fast_voice_type = await clone_voice(resolved, payload, text, voice_name)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"fast_voice_type": fast_voice_type}

    @app.websocket("/ws")
    async def voice_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        missing = resolved.missing_required()
        if missing:
            await websocket.send_json(
                {"type": "error", "message": "缺少配置：" + "、".join(missing)}
            )
            await websocket.close()
            return
        task = None
        try:
            task = build_session(websocket, resolved, music)
            _cancel_pipeline_on_disconnect(task)
            runner = PipelineRunner(handle_sigint=False)
            await runner.run(task)
        finally:
            if task is not None and not task.has_finished():
                await task.cancel()

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app


def _public_track(track: MusicTrack) -> dict[str, str]:
    return {
        "id": track.id,
        "title": track.title,
        "artist": track.artist,
        "url": track.url,
    }


def _find_track(library: MusicLibrary, track_id: str) -> MusicTrack | None:
    for track in library.ensure_seed_tracks():
        if track.id == track_id and track.wav_path.is_file():
            return track
    return None


def _cancel_pipeline_on_disconnect(task) -> None:
    """传输层发现断开后取消会话。管道内部才拿得到那条 WebSocket。"""
    transport = _websocket_transport(task)
    if transport is None:
        return

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(_transport, _websocket) -> None:
        await task.cancel()


def _websocket_transport(task):
    pending = [task.pipeline]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(node, FastAPIWebsocketInputTransport):
            return node._transport
        children = getattr(node, "processors", None)
        if isinstance(children, (list, tuple)):
            pending.extend(children)
    return None


def _load_root_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_ROOT_ENV, override=False)


_load_root_env()
app = create_app()


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
