# voice-agent

本地实时通话服务。进程启动时由 `load_settings()` 读取 `os.getenv`，`server.py` 不会加载 `.env` 或 `.env.example`。未写入当前进程环境的变量视为空。

没有配齐腾讯云和大模型密钥时，ASR / TTS / LLM 不会接通。不要假设通话已经可用。

## 环境变量

在启动 uvicorn 的同一个 shell 里导出，或写在命令前面：

```bash
export TENCENT_APP_ID=你的应用ID
export TENCENT_SECRET_ID=你的密钥ID
export TENCENT_SECRET_KEY=你的密钥
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=你的大模型密钥
export LLM_MODEL=deepseek-chat
```

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

## 音乐模式

页面或小程序把 `music_mode` 随 `config` 发给服务。

- `client_url`：客户端另开一个音频元素，拉 `/api/music/stream/{id}`。说话时背景音乐音量降到 0.2。
- `server_mix`：音乐混进同一路下行 PCM。客户端不再打开第二个音频元素。

说「放一首深海的音乐」会点到预置曲目「深海冥想」。

## 微信小程序

用微信开发者工具导入 `voice_agent/miniprogram`。`project.config.json` 里 `urlCheck` 已是 `false`，可连 `ws://127.0.0.1:8765/ws`。真机必须换成已配置的 `wss` 域名，本机 `ws://` 不可用。

## 验收

1. 未配密钥时，页面或 `/api/health` 给出中文缺项列表，不是栈追踪。
2. 配齐密钥后说话，字幕先出识别中文本，再出最终文本和语音。
3. 助手说话时插话，播放在一次往返内停止。
4. 说「放一首深海的音乐」，`client_url` 出现曲目卡片，说话时音量下降。
5. `server_mix` 不再打开第二个音频元素，下行 PCM 仍连续。
6. 微信开发者工具连接 `ws://127.0.0.1:8765/ws`，完成同样的说话、打断和点歌。
