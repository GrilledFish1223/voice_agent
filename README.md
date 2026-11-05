# voice-agent

本地实时通话服务。`server.py` 启动时会读取仓库根目录的 `.env`，已存在的环境变量优先。`load_settings()` 再从进程环境取值。`.env.example` 不会被读取。

没有配齐腾讯云和大模型密钥时，ASR / TTS / LLM 不会接通。不要假设通话已经可用。

## 环境变量

写在仓库根目录 `.env`，或在启动 uvicorn 的同一个 shell 里导出：

```bash
export TENCENT_CLOUD_APP_ID=你的应用ID
export TENCENT_CLOUD_SECRET_ID=你的密钥ID
export TENCENT_CLOUD_SECRET_KEY=你的密钥
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=你的大模型密钥
export LLM_MODEL=deepseek-flash
```

也接受旧名字 `TENCENT_APP_ID`、`TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`。同名时以 `TENCENT_CLOUD_*` 为准。

`voice_agent/.env.example` 只列出上述名字和示例值，复制该文件不会自动生效。

缺项时 `GET /api/health` 返回 `{"ok": true, "missing": ["TENCENT_APP_ID", ...]}`。开始通话后，页面或小程序显示「缺少配置：」加上这些名字，不会抛栈。

## 安装与启动

依赖装进仓库已有的 `.venv`。不要用 `.venv/bin/python -m pip`，该入口不可用。

```bash
uv pip install -r voice_agent/requirements.txt
```

在仓库根目录启动：

```bash
.venv/bin/python -m uvicorn voice_agent.server:app --host 127.0.0.1 --port 8765
```

浏览器打开 http://127.0.0.1:8765

## 协议

Web 和小程序共用同一条 WebSocket：`/ws`。

- 二进制：16 kHz Int16 PCM
- 文本：JSON（配置、字幕、打断、音乐控制、错误）

## 会话模式

Pipecat 管道在 `transport.input()` 后加了 `ConversationMicGate`：

- **对话模式**：麦克风 PCM 进入 VAD / ASR / LLM / TTS。
- **音乐模式**：开始播放后暂停拾音，上行 PCM 不再进识别，避免把扬声器里的歌再听成说话。点暂停、停止或挂断后回到对话。

`music_mode` 只决定音乐怎么出声，不决定是否拾音：

- `client_url`：客户端另开一个音频元素，拉 `/api/music/stream/{id}`。
- `server_mix`：音乐混进同一路下行 PCM。客户端不再打开第二个音频元素。

说「放一首深海的音乐」会点到预置曲目「深海冥想」。停歌请点暂停，音乐模式下麦克风不送识别。

## 微信小程序

用微信开发者工具导入 `voice_agent/miniprogram`。`project.config.json` 里 `urlCheck` 已是 `false`，可连 `ws://127.0.0.1:8765/ws`。真机必须换成已配置的 `wss` 域名，本机 `ws://` 不可用。

## 验收

1. 未配密钥时，页面或 `/api/health` 给出中文缺项列表，不是栈追踪。
2. 配齐密钥后说话，字幕先出识别中文本，再出最终文本和语音。
3. 助手说话时插话，播放在一次往返内停止。
4. 说「放一首深海的音乐」后进入音乐模式，麦克风不再送识别；点暂停后回到对话。
5. `server_mix` 不再打开第二个音频元素，下行 PCM 仍连续。
6. 微信开发者工具连接 `ws://127.0.0.1:8765/ws`，完成同样的说话、打断和点歌。
