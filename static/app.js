const startButton = document.querySelector("#start-call");
const endButton = document.querySelector("#end-call");
const interruptButton = document.querySelector("#interrupt-call");
const callState = document.querySelector("#call-state");
const connPill = document.querySelector("#conn-pill");
const activeVoicePill = document.querySelector("#active-voice-pill");
const turnState = document.querySelector("#turn-state");
const latencyBadge = document.querySelector("#latency-badge");
const latencyParts = document.querySelector("#latency-parts");
const asrFinal = document.querySelector("#asr-final");
const asrInterim = document.querySelector("#asr-interim");
const llmReply = document.querySelector("#llm-reply");
const userLiveCard = document.querySelector("#user-live-card");
const botLiveCard = document.querySelector("#bot-live-card");
const userRmsTag = document.querySelector("#user-rms-tag");
const botStateTag = document.querySelector("#bot-state-tag");
const chatStream = document.querySelector("#chat-stream");
const chatEmpty = document.querySelector("#chat-empty");
const errorBanner = document.querySelector("#error-banner");

const systemPrompt = document.querySelector("#system-prompt");
const voiceType = document.querySelector("#voice-type");
const voiceGallery = document.querySelector("#voice-gallery");
const fastVoiceType = document.querySelector("#fast-voice-type");
const speedInput = document.querySelector("#speed-input");
const vadStop = document.querySelector("#vad-stop");
const vadStopValue = document.querySelector("#vad-stop-value");
const musicMode = document.querySelector("#music-mode");
const musicModeNote = document.querySelector("#music-mode-note");

const trackTitle = document.querySelector("#track-title");
const trackArtist = document.querySelector("#track-artist");
const vinylDisc = document.querySelector("#vinyl-disc");
const duckingBadge = document.querySelector("#ducking-badge");
const musicPlayButton = document.querySelector("#music-play");
const musicPauseButton = document.querySelector("#music-pause");
const musicNextButton = document.querySelector("#music-next");

const cloneName = document.querySelector("#clone-name");
const cloneText = document.querySelector("#clone-text");
const cloneAudio = document.querySelector("#clone-audio");
const cloneSubmit = document.querySelector("#clone-submit");
const cloneResult = document.querySelector("#clone-result");
const orbCanvas = document.querySelector("#orb-canvas");

let socket = null;
let audioContext = null;
let micStream = null;
let captureNode = null;
let playbackNode = null;
let musicAudio = null;
let duckTimer = null;
let configTimer = null;
let userSpeaking = false;
let botSpeaking = false;
let replyFinished = true;
let openUserBubble = null;
let openBotBubble = null;
let tracks = [];
let voicesList = [];
let currentTrack = null;
let restingMusicVolume = 0.8;
let sessionMode = "conversation";

// 声波能量球动画状态
let userRms = 0;
let botRms = 0;
let orbPhase = 0;

function computePcmRms(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 2) {
    return 0;
  }
  const view = new Int16Array(buffer);
  let sum = 0;
  const step = Math.max(1, Math.floor(view.length / 160));
  let count = 0;
  for (let i = 0; i < view.length; i += step) {
    const norm = view[i] / 32768;
    sum += norm * norm;
    count += 1;
  }
  return count > 0 ? Math.min(1, Math.sqrt(sum / count) * 3.2) : 0;
}

function startOrbVisualizer() {
  if (!orbCanvas) {
    return;
  }
  const ctx = orbCanvas.getContext("2d");
  const width = orbCanvas.width;
  const height = orbCanvas.height;
  const cx = width / 2;
  const cy = height / 2;

  function render() {
    orbPhase += 0.035;
    userRms *= 0.88;
    botRms *= 0.88;

    ctx.clearRect(0, 0, width, height);

    const activeEnergy = botSpeaking
      ? Math.max(0.22, botRms)
      : userSpeaking
        ? Math.max(0.2, userRms)
        : Math.max(userRms, botRms) * 0.5;

    let primaryColor = "56, 189, 248";   // Cyan
    let secondaryColor = "129, 140, 248"; // Indigo
    if (userSpeaking) {
      primaryColor = "16, 185, 129";     // Emerald
      secondaryColor = "56, 189, 248";
    } else if (botSpeaking) {
      primaryColor = "56, 189, 248";
      secondaryColor = "236, 72, 153";   // Pink/Magenta
    }

    // 外层呼吸光晕
    const baseRadius = 74 + activeEnergy * 42;
    const glow = ctx.createRadialGradient(cx, cy, 12, cx, cy, baseRadius * 1.9);
    glow.addColorStop(0, `rgba(${primaryColor}, ${0.42 + activeEnergy * 0.35})`);
    glow.addColorStop(0.55, `rgba(${secondaryColor}, ${0.16 + activeEnergy * 0.2})`);
    glow.addColorStop(1, "rgba(7, 10, 20, 0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius * 1.9, 0, Math.PI * 2);
    ctx.fill();

    // 3 层流体正弦波环
    for (let ring = 0; ring < 3; ring += 1) {
      ctx.beginPath();
      const ringRadius = baseRadius * (0.78 + ring * 0.16);
      const freq = 4 + ring * 2;
      const amp = (4 + ring * 3) + activeEnergy * (22 + ring * 8);
      for (let angle = 0; angle <= Math.PI * 2 + 0.05; angle += 0.06) {
        const wave =
          Math.sin(angle * freq + orbPhase * (1.4 + ring * 0.4)) * amp * 0.6 +
          Math.cos(angle * (freq - 1) - orbPhase * 1.1) * amp * 0.4;
        const r = ringRadius + wave;
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        if (angle === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.closePath();
      ctx.strokeStyle =
        ring === 0
          ? `rgba(${primaryColor}, 0.85)`
          : `rgba(${secondaryColor}, ${0.55 - ring * 0.15})`;
      ctx.lineWidth = 2.4 - ring * 0.5;
      ctx.stroke();
    }

    // 核心发光球体
    const coreGrad = ctx.createRadialGradient(
      cx - 16,
      cy - 16,
      6,
      cx,
      cy,
      baseRadius * 0.72
    );
    coreGrad.addColorStop(0, "#ffffff");
    coreGrad.addColorStop(0.35, `rgba(${primaryColor}, 0.92)`);
    coreGrad.addColorStop(1, `rgba(${secondaryColor}, 0.25)`);
    ctx.fillStyle = coreGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius * 0.68, 0, Math.PI * 2);
    ctx.fill();

    requestAnimationFrame(render);
  }

  requestAnimationFrame(render);
}

function normalizeCaption(text) {
  return (text || "").replace(/[。！？!?.,，、；;：:\s“”"'（）()]/g, "");
}

function captionsOverlap(left, right) {
  const first = normalizeCaption(left);
  const second = normalizeCaption(right);
  return Boolean(first && second && (first === second || first.includes(second) || second.includes(first)));
}

function richerCaption(left, right) {
  return normalizeCaption(right).length >= normalizeCaption(left).length ? right : left;
}

function appendChatBubble(role, text) {
  const clean = (text || "").trim();
  if (!chatStream) {
    return null;
  }
  if (chatEmpty) {
    chatEmpty.hidden = true;
  }
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble " + role;
  const meta = document.createElement("div");
  meta.className = "chat-bubble-meta";
  meta.textContent = role === "user" ? "🧑 你" : "🤖 语音助手";
  const content = document.createElement("div");
  content.className = "chat-bubble-text";
  content.textContent = clean;
  bubble.appendChild(meta);
  bubble.appendChild(content);
  chatStream.appendChild(bubble);
  chatStream.scrollTop = chatStream.scrollHeight;
  return content;
}

function liveBubble(role) {
  if (role === "user" && openUserBubble) {
    return openUserBubble;
  }
  if (role === "bot" && openBotBubble) {
    return openBotBubble;
  }
  const content = appendChatBubble(role, "");
  if (role === "user") {
    openUserBubble = content;
  } else {
    openBotBubble = content;
  }
  return content;
}

function writeLiveBubble(role, text, commit) {
  let content = role === "user" ? openUserBubble : openBotBubble;
  if (!content && chatStream) {
    const last = chatStream.lastElementChild;
    if (last && last.classList.contains(role)) {
      const previous = last.querySelector(".chat-bubble-text");
      if (previous && captionsOverlap(previous.textContent, text)) {
        content = previous;
      }
    }
  }
  if (!content) {
    content = liveBubble(role);
  }
  if (!content) {
    return;
  }
  const next = (text || "").trim();
  if (next) {
    content.textContent = richerCaption(content.textContent || "", next);
  }
  if (chatStream) {
    chatStream.scrollTop = chatStream.scrollHeight;
  }
  if (commit) {
    if (role === "user") {
      openUserBubble = null;
    } else {
      openBotBubble = null;
    }
  }
}

function websocketUrl() {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return scheme + "//" + window.location.host + "/ws";
}

function setCallState(text, live) {
  callState.textContent = text;
  if (connPill) {
    connPill.dataset.live = live ? "true" : "false";
  }
}

function showError(text) {
  if (!text) {
    errorBanner.hidden = true;
    errorBanner.textContent = "";
    return;
  }
  errorBanner.hidden = false;
  errorBanner.textContent = "⚠️ " + text;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function renderMetrics(message) {
  if (latencyBadge && message.user_to_bot_secs != null) {
    latencyBadge.hidden = false;
    latencyBadge.textContent = "端到端 " + Number(message.user_to_bot_secs).toFixed(2) + "s";
  }
  if (latencyParts && Array.isArray(message.parts) && message.parts.length) {
    latencyParts.hidden = false;
    latencyParts.textContent = message.parts
      .map((part) => (part.label || part.key) + " " + Number(part.duration_secs).toFixed(2) + "s")
      .join(" · ");
  } else if (latencyParts && Array.isArray(message.items) && message.items.length) {
    const lines = message.items
      .map((item) => {
        const value = item.ttfat ?? item.ttfa ?? item.ttfb ?? item.value;
        if (value == null) {
          return "";
        }
        return (item.kind || "metric") + " " + Number(value).toFixed(2) + "s";
      })
      .filter(Boolean);
    if (lines.length) {
      latencyParts.hidden = false;
      latencyParts.textContent = lines.join(" · ");
    }
  }
}

function applySessionMode(mode) {
  sessionMode = mode === "music" ? "music" : "conversation";
  if (sessionMode === "conversation") {
    userSpeaking = false;
  }
  updateTurnLabel();
  renderSessionBadge();
}

function renderSessionBadge() {
  if (!duckingBadge) {
    return;
  }
  if (sessionMode === "music") {
    duckingBadge.dataset.ducking = "true";
    duckingBadge.textContent = "🎵 音乐模式 · 拾音已关";
    return;
  }
  duckingBadge.dataset.ducking = "false";
  duckingBadge.textContent = "💬 对话模式";
}

function updateTurnLabel() {
  if (userLiveCard) {
    userLiveCard.dataset.active = userSpeaking ? "true" : "false";
  }
  if (botLiveCard) {
    botLiveCard.dataset.active = botSpeaking ? "true" : "false";
  }
  if (botStateTag) {
    botStateTag.textContent = botSpeaking ? "🔊 正在播报" : "待命";
  }
  if (sessionMode === "music") {
    turnState.textContent = "🎵 音乐模式 · 已暂停麦克风拾音，点暂停回到对话";
    turnState.dataset.mode = "music";
    return;
  }
  if (userSpeaking) {
    turnState.textContent = "🟢 正在听你说话（已自动打断旧语音）";
    turnState.dataset.mode = "user";
    return;
  }
  if (botSpeaking) {
    turnState.textContent = "🔵 助手正在回答（随时开口即可打断）";
    turnState.dataset.mode = "bot";
    return;
  }
  turnState.textContent =
    socket && socket.readyState === WebSocket.OPEN
      ? "✨ 通话已接通 · 请直接说话"
      : "待机中 · 点击下方「开始通话」直接开口";
  turnState.dataset.mode = "idle";
}

function currentConfigPayload() {
  return {
    type: "config",
    system_prompt: systemPrompt.value.trim(),
    voice_type: Number(voiceType.value || 602003),
    fast_voice_type: fastVoiceType.value.trim(),
    speed: Number(speedInput.value || 0),
    volume: 0,
    vad_stop_secs: Number(vadStop.value || 0.2),
    vad_confidence: 0.7,
    llm_model: "",
    music_mode: musicMode.value,
  };
}

function updateActiveVoiceUI() {
  const currentId = Number(voiceType.value || 602003);
  const found = voicesList.find((v) => Number(v.voice_type) === currentId);
  if (activeVoicePill) {
    activeVoicePill.textContent = found
      ? `🎙️ ${found.name} (${found.voice_type})`
      : `🎙️ 音色 ${currentId}`;
  }
  if (voiceGallery) {
    Array.from(voiceGallery.children).forEach((btn) => {
      btn.dataset.selected = Number(btn.dataset.voiceId) === currentId ? "true" : "false";
    });
  }
}

function sendJson(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return false;
  }
  socket.send(JSON.stringify(payload));
  return true;
}

function sendConfig() {
  updateActiveVoiceUI();
  sendJson(currentConfigPayload());
}

function queueConfig() {
  updateActiveVoiceUI();
  if (configTimer) {
    clearTimeout(configTimer);
  }
  configTimer = window.setTimeout(sendConfig, 150);
}

function clearPlaybackBuffer() {
  if (playbackNode) {
    playbackNode.port.postMessage("clear");
  }
  botRms = 0;
}

function musicModeValue() {
  return musicMode.value === "server_mix" ? "server_mix" : "client_url";
}

function releaseMusicAudio() {
  if (duckTimer) {
    clearTimeout(duckTimer);
    duckTimer = null;
  }
  if (!musicAudio) {
    return;
  }
  musicAudio.pause();
  musicAudio.removeAttribute("src");
  musicAudio.load();
  musicAudio = null;
  if (vinylDisc) {
    vinylDisc.dataset.playing = "false";
  }
}

function ensureMusicAudio() {
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    return null;
  }
  if (!musicAudio) {
    musicAudio = new Audio();
    musicAudio.loop = true;
    musicAudio.volume = restingMusicVolume;
    musicAudio.addEventListener("play", () => {
      if (vinylDisc) vinylDisc.dataset.playing = "true";
    });
    musicAudio.addEventListener("pause", () => {
      if (vinylDisc) vinylDisc.dataset.playing = "false";
    });
  }
  return musicAudio;
}

function normalizeMusicVolume(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return 0.8;
  }
  return Math.max(0, Math.min(1, parsed));
}

function duckedMusicVolume() {
  if (restingMusicVolume === 0.8) {
    return 0.2;
  }
  return Math.max(0, Math.min(1, restingMusicVolume * 0.25));
}

function applyMusicDucking() {
  if (duckTimer) {
    clearTimeout(duckTimer);
    duckTimer = null;
  }
  renderSessionBadge();
  if (!musicAudio) {
    return;
  }
  if (userSpeaking || botSpeaking) {
    musicAudio.volume = duckedMusicVolume();
    updateDuckingBadge(true);
    return;
  }
  duckTimer = window.setTimeout(() => {
    if (musicAudio && !userSpeaking && !botSpeaking) {
      musicAudio.volume = restingMusicVolume;
      updateDuckingBadge(false);
    }
  }, 800);
}

function renderTrack() {
  if (!currentTrack) {
    trackTitle.textContent = "尚未播放曲目";
    trackArtist.textContent = "对我说「播放一首轻音乐」或点击下方按钮";
    return;
  }
  trackTitle.textContent = currentTrack.title || currentTrack.id || "未知曲目";
  trackArtist.textContent = currentTrack.artist || "LaunchKit Synth";
}

function playTrackInBrowser(track) {
  if (!track) {
    return;
  }
  currentTrack = track;
  renderTrack();
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    if (vinylDisc) vinylDisc.dataset.playing = "true";
    return;
  }
  const audio = ensureMusicAudio();
  if (!audio || !track.url) {
    return;
  }
  if (audio.dataset.trackUrl !== track.url) {
    audio.src = track.url;
    audio.dataset.trackUrl = track.url;
  }
  audio.volume = userSpeaking || botSpeaking ? duckedMusicVolume() : restingMusicVolume;
  audio.play().catch(() => {});
}

function nextLocalTrack() {
  if (!tracks.length) {
    return null;
  }
  if (!currentTrack) {
    return tracks[0];
  }
  const index = tracks.findIndex((item) => item.id === currentTrack.id);
  if (index === -1) {
    return tracks[0];
  }
  return tracks[(index + 1) % tracks.length];
}

function handleMusicControl(message) {
  if (message.session_mode) {
    applySessionMode(message.session_mode);
  } else if (message.action === "pause" || message.action === "stop") {
    applySessionMode("conversation");
  } else if (message.action === "play" || message.action === "resume" || message.action === "next") {
    applySessionMode("music");
  }
  const action = message.action;
  if (message.volume !== undefined && message.volume !== null) {
    restingMusicVolume = normalizeMusicVolume(message.volume);
  }
  if (message.track) {
    currentTrack = message.track;
    renderTrack();
  }
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    if (vinylDisc) {
      vinylDisc.dataset.playing = action === "pause" || action === "stop" ? "false" : "true";
    }
    return;
  }
  if (action === "play") {
    playTrackInBrowser(message.track || currentTrack || nextLocalTrack());
    return;
  }
  if (action === "next") {
    playTrackInBrowser(message.track || nextLocalTrack());
    return;
  }
  if (!musicAudio) {
    return;
  }
  if (action === "pause") {
    musicAudio.pause();
    return;
  }
  if (action === "resume") {
    applyMusicDucking();
    musicAudio.play().catch(() => {});
    return;
  }
  if (action === "stop") {
    musicAudio.pause();
    musicAudio.currentTime = 0;
    return;
  }
  if (action === "volume_up" || action === "volume_down") {
    applyMusicDucking();
  }
}

function handleServerEvent(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch (_error) {
    return;
  }
  if (!message || typeof message.type !== "string") {
    return;
  }
  if (message.type === "clear_audio") {
    clearPlaybackBuffer();
    return;
  }
  if (message.type === "user_speaking") {
    userSpeaking = Boolean(message.active);
    if (userSpeaking) {
      clearPlaybackBuffer();
      openUserBubble = null;
      asrInterim.textContent = "";
    }
    updateTurnLabel();
    applyMusicDucking();
    return;
  }
  if (message.type === "bot_speaking") {
    botSpeaking = Boolean(message.active);
    updateTurnLabel();
    applyMusicDucking();
    return;
  }
  if (message.type === "user_transcript" || message.type === "asr_interim" || message.type === "asr_final") {
    const text = (message.text || "").trim();
    const isFinal = message.type === "asr_final" || message.final === true;
    if (!text) {
      return;
    }
    if (isFinal) {
      asrFinal.textContent = text;
      asrInterim.textContent = "";
      writeLiveBubble("user", text, true);
    } else {
      asrInterim.textContent = "识别中：" + text;
      writeLiveBubble("user", text, false);
    }
    return;
  }
  if (message.type === "bot_output" || message.type === "llm_delta" || message.type === "llm_done") {
    const isFinal = message.type === "llm_done" || message.final === true;
    if (!isFinal) {
      if (replyFinished) {
        llmReply.textContent = "";
        replyFinished = false;
        openBotBubble = null;
      }
      llmReply.textContent += message.text || "";
      writeLiveBubble("bot", llmReply.textContent, false);
      return;
    }
    const replyText = (message.text || llmReply.textContent || "").trim();
    if (replyText) {
      llmReply.textContent = replyText;
      writeLiveBubble("bot", replyText, true);
    }
    replyFinished = true;
    return;
  }
  if (message.type === "metrics") {
    renderMetrics(message);
    return;
  }
  if (message.type === "session_mode") {
    applySessionMode(message.mode);
    return;
  }
  if (message.type === "music_control") {
    handleMusicControl(message);
    return;
  }
  if (message.type === "error") {
    showError(message.message || "通话发生错误");
  }
}

async function openAudioGraph() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
    video: false,
  });
  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("/static/audio-worklet.js");
  await audioContext.resume();

  const source = audioContext.createMediaStreamSource(micStream);
  captureNode = new AudioWorkletNode(audioContext, "pcm-capture");
  playbackNode = new AudioWorkletNode(audioContext, "pcm-playback");

  const muteTap = audioContext.createGain();
  muteTap.gain.value = 0;

  captureNode.port.onmessage = (event) => {
    if (!(event.data instanceof ArrayBuffer)) {
      return;
    }
    if (sessionMode === "music") {
      userRms *= 0.7;
      return;
    }
    userRms = computePcmRms(event.data);
    if (userRmsTag) {
      const db = userRms > 0.001 ? Math.round(20 * Math.log10(userRms)) : -60;
      userRmsTag.textContent = `${db} dB`;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(event.data);
    }
  };

  source.connect(captureNode);
  captureNode.connect(muteTap);
  muteTap.connect(audioContext.destination);
  playbackNode.connect(audioContext.destination);
}

async function startCall() {
  clearError();
  startButton.disabled = true;
  setCallState("正在打开麦克风…", false);
  try {
    await openAudioGraph();
    const activeSocket = new WebSocket(websocketUrl());
    socket = activeSocket;
    activeSocket.binaryType = "arraybuffer";
    setCallState("正在连接语音服务…", false);

    activeSocket.onopen = () => {
      if (socket !== activeSocket) {
        return;
      }
      setCallState("通话中", true);
      endButton.disabled = false;
      interruptButton.disabled = false;
      updateTurnLabel();
      sendConfig();
    };

    activeSocket.onmessage = (event) => {
      if (typeof event.data === "string") {
        handleServerEvent(event.data);
        return;
      }
      if (event.data instanceof ArrayBuffer && playbackNode) {
        botRms = computePcmRms(event.data);
        playbackNode.port.postMessage(event.data, [event.data]);
      }
    };

    activeSocket.onerror = () => {
      if (socket === activeSocket) {
        showError("语音 WebSocket 连接异常");
      }
    };

    activeSocket.onclose = () => {
      if (socket !== activeSocket) {
        return;
      }
      socket = null;
      void releaseCallMedia();
    };
  } catch (error) {
    const denied = error && error.name === "NotAllowedError";
    showError(denied ? "麦克风权限被拒绝，请在浏览器地址栏允许麦克风访问" : error.message || "无法启动通话");
    await endCall();
  }
}

async function releaseCallMedia() {
  userSpeaking = false;
  botSpeaking = false;
  userRms = 0;
  botRms = 0;
  clearPlaybackBuffer();

  const stream = micStream;
  const context = audioContext;
  micStream = null;
  audioContext = null;
  captureNode = null;
  playbackNode = null;

  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
  }
  if (context && context.state !== "closed") {
    await context.close().catch(() => {});
  }

  currentTrack = null;
  applySessionMode("conversation");
  releaseMusicAudio();
  renderTrack();
  applyMusicDucking();
  setCallState("未连接", false);
  startButton.disabled = false;
  endButton.disabled = true;
  interruptButton.disabled = true;
  updateTurnLabel();
}

async function endCall() {
  if (configTimer) {
    clearTimeout(configTimer);
    configTimer = null;
  }
  const activeSocket = socket;
  socket = null;
  if (activeSocket && activeSocket.readyState < WebSocket.CLOSING) {
    activeSocket.close();
  }
  await releaseCallMedia();
}

function interruptCall() {
  clearPlaybackBuffer();
  sendJson({ type: "interrupt" });
}

function sendMusicCommand(action) {
  if (action === "play" || action === "resume" || action === "next") {
    applySessionMode("music");
  } else if (action === "pause" || action === "stop") {
    applySessionMode("conversation");
  }
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    sendJson({ type: "music_cmd", action, volume: restingMusicVolume });
    return;
  }
  const sent = sendJson({ type: "music_cmd", action, volume: restingMusicVolume });
  if (sent) {
    return;
  }
  if (action === "play" || action === "resume") {
    playTrackInBrowser(currentTrack || nextLocalTrack());
  } else if (action === "pause" && musicAudio) {
    musicAudio.pause();
  } else if (action === "next") {
    playTrackInBrowser(nextLocalTrack());
  }
}

function refreshMusicModeNote() {
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    musicModeNote.textContent = "服务端混音 · 播放时关拾音，暂停后回到对话";
    return;
  }
  musicModeNote.textContent = "独立音轨 · 播放时关拾音，暂停后回到对话";
}

function renderVoiceGallery(voices) {
  if (!voiceGallery) return;
  voiceGallery.replaceChildren();
  voices.forEach((voice) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "voice-card-btn";
    btn.dataset.voiceId = String(voice.voice_type);
    btn.innerHTML = `
      <span class="voice-card-name">${voice.name}</span>
      <span class="voice-card-scene">${voice.scene}</span>
      <span class="voice-card-id">ID: ${voice.voice_type}</span>
    `;
    btn.addEventListener("click", () => {
      voiceType.value = String(voice.voice_type);
      fastVoiceType.value = "";
      queueConfig();
    });
    voiceGallery.appendChild(btn);
  });
}

async function loadVoices() {
  const response = await fetch("/api/voices");
  if (!response.ok) {
    throw new Error("音色列表加载失败");
  }
  const voices = await response.json();
  voicesList = voices;
  voiceType.replaceChildren();
  voices.forEach((voice) => {
    const option = document.createElement("option");
    option.value = String(voice.voice_type);
    option.textContent = `${voice.voice_type} · ${voice.name} (${voice.scene})`;
    voiceType.appendChild(option);
  });
  const preferred = voices.find((voice) => Number(voice.voice_type) === 602003);
  if (preferred) {
    voiceType.value = "602003";
  }
  renderVoiceGallery(voices);
  updateActiveVoiceUI();
}

async function loadTracks() {
  const response = await fetch("/api/music");
  if (!response.ok) {
    throw new Error("曲库加载失败");
  }
  tracks = await response.json();
  currentTrack = tracks[0] || null;
  renderTrack();
}

async function cloneVoice() {
  const file = cloneAudio.files && cloneAudio.files[0];
  if (!file) {
    showError("请先选择一段 16kHz WAV 音频文件");
    return;
  }
  if (!cloneText.value.trim()) {
    showError("请填写录音里朗读的对照文本");
    return;
  }
  clearError();
  cloneSubmit.disabled = true;
  cloneResult.textContent = "正在提交腾讯云声音复刻…";
  try {
    const body = new FormData();
    body.append("audio", file);
    body.append("text", cloneText.value.trim());
    body.append("voice_name", cloneName.value.trim() || "专属音色");
    const response = await fetch("/api/clone-voice", {
      method: "POST",
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "声音复刻失败");
    }
    fastVoiceType.value = payload.fast_voice_type || "";
    cloneResult.textContent = "✅ 复刻成功：" + fastVoiceType.value;
    sendConfig();
  } catch (error) {
    cloneResult.textContent = "";
    showError(error.message || "声音复刻失败");
  } finally {
    cloneSubmit.disabled = false;
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    if (Array.isArray(payload.missing) && payload.missing.length > 0) {
      showError("缺少配置：" + payload.missing.join("、"));
    }
  } catch (_error) {
    showError("无法连接本地语音服务");
  }
}

startButton.addEventListener("click", () => {
  void startCall();
});

endButton.addEventListener("click", () => {
  void endCall();
});

interruptButton.addEventListener("click", interruptCall);

musicPlayButton.addEventListener("click", () => {
  sendMusicCommand("play");
});

musicPauseButton.addEventListener("click", () => {
  sendMusicCommand("pause");
});

musicNextButton.addEventListener("click", () => {
  sendMusicCommand("next");
});

document.querySelectorAll("[data-quick-track]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const id = btn.getAttribute("data-quick-track");
    const found = tracks.find((t) => t.id === id);
    if (found) {
      playTrackInBrowser(found);
    }
  });
});

document.querySelectorAll("[data-prompt]").forEach((btn) => {
  btn.addEventListener("click", () => {
    systemPrompt.value = btn.getAttribute("data-prompt") || "";
    queueConfig();
  });
});

vadStop.addEventListener("input", () => {
  vadStopValue.textContent = Number(vadStop.value).toFixed(2) + "s";
  queueConfig();
});

musicMode.addEventListener("change", () => {
  refreshMusicModeNote();
  queueConfig();
});

[systemPrompt, voiceType, fastVoiceType, speedInput].forEach((element) => {
  element.addEventListener("input", queueConfig);
  element.addEventListener("change", queueConfig);
});

cloneSubmit.addEventListener("click", () => {
  void cloneVoice();
});

refreshMusicModeNote();
updateTurnLabel();
startOrbVisualizer();
void checkHealth();
void loadVoices().catch((error) => showError(error.message));
void loadTracks().catch((error) => showError(error.message));
