const startButton = document.querySelector("#start-call");
const endButton = document.querySelector("#end-call");
const interruptButton = document.querySelector("#interrupt");
const stage = document.querySelector("#stage");
const statusText = document.querySelector("#status-text");
const asrLive = document.querySelector("#asr-live");
const asrLog = document.querySelector("#asr-log");
const replyLive = document.querySelector("#reply-live");
const replyLog = document.querySelector("#reply-log");
const errorBanner = document.querySelector("#error-banner");
const systemPrompt = document.querySelector("#system-prompt");
const voiceType = document.querySelector("#voice-type");
const fastVoiceType = document.querySelector("#fast-voice-type");
const speedInput = document.querySelector("#speed");
const vadStop = document.querySelector("#vad-stop");
const vadStopValue = document.querySelector("#vad-stop-value");
const musicMode = document.querySelector("#music-mode");
const musicModeNote = document.querySelector("#music-mode-note");
const trackTitle = document.querySelector("#track-title");
const trackArtist = document.querySelector("#track-artist");
const trackState = document.querySelector("#track-state");
const playButton = document.querySelector("#music-play");
const pauseButton = document.querySelector("#music-pause");
const nextButton = document.querySelector("#music-next");
const cloneAudio = document.querySelector("#clone-audio");
const cloneText = document.querySelector("#clone-text");
const cloneName = document.querySelector("#clone-name");
const cloneSubmit = document.querySelector("#clone-submit");
const cloneResult = document.querySelector("#clone-result");

let socket = null;
let audioContext = null;
let micStream = null;
let captureNode = null;
let playbackNode = null;
let musicAudio = null;
let tracks = [];
let currentTrack = null;
let userSpeaking = false;
let botSpeaking = false;
let duckTimer = null;
let configTimer = null;
let starting = false;
let replyBuffer = "";

function musicModeValue() {
  return musicMode.value === "server_mix" ? "server_mix" : "client_url";
}

function setStatus(text, live) {
  statusText.textContent = text;
  stage.classList.toggle("is-live", Boolean(live));
}

function showError(text) {
  errorBanner.hidden = false;
  errorBanner.textContent = text;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function appendLog(target, text) {
  const line = text.trim();
  if (!line) {
    return;
  }
  const paragraph = document.createElement("p");
  paragraph.textContent = line;
  target.appendChild(paragraph);
  while (target.children.length > 8) {
    target.removeChild(target.firstChild);
  }
}

function sendJson(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify(payload));
}

function buildConfig() {
  const speed = Number(speedInput.value);
  const stopSecs = Number(vadStop.value);
  return {
    type: "config",
    system_prompt: systemPrompt.value,
    voice_type: Number(voiceType.value || 101001),
    fast_voice_type: fastVoiceType.value.trim(),
    speed: Number.isFinite(speed) ? speed : 0,
    volume: 0,
    vad_stop_secs: Number.isFinite(stopSecs) ? stopSecs : 0.5,
    vad_confidence: 0.7,
    llm_model: "",
    music_mode: musicModeValue(),
  };
}

function sendConfig() {
  sendJson(buildConfig());
}

function queueConfig() {
  window.clearTimeout(configTimer);
  configTimer = window.setTimeout(sendConfig, 200);
}

function renderTrack() {
  trackTitle.textContent = currentTrack ? currentTrack.title : "还没有曲目";
  trackArtist.textContent = currentTrack ? currentTrack.artist || "" : "";
}

function setTrackState(text) {
  trackState.textContent = text;
}

function absoluteUrl(url) {
  return new URL(url, location.href).href;
}

function releaseMusicAudio() {
  if (!musicAudio) {
    return;
  }
  musicAudio.pause();
  musicAudio.removeAttribute("src");
  musicAudio.load();
  musicAudio = null;
}

function applyMusicVolume() {
  const audio = musicAudio;
  if (!audio) {
    return;
  }
  if (userSpeaking || botSpeaking) {
    window.clearTimeout(duckTimer);
    duckTimer = null;
    audio.volume = 0.2;
    return;
  }
  window.clearTimeout(duckTimer);
  duckTimer = window.setTimeout(() => {
    if (userSpeaking || botSpeaking || !musicAudio) {
      return;
    }
    musicAudio.volume = 0.8;
  }, 800);
}

function startSeparateTrack(url) {
  if (musicModeValue() === "server_mix" || !url) {
    return null;
  }
  const audio = new Audio(url);
  audio.loop = true;
  audio.preload = "auto";
  if (musicAudio) {
    musicAudio.pause();
  }
  musicAudio = audio;
  if (userSpeaking || botSpeaking) {
    audio.volume = 0.2;
  } else {
    audio.volume = 0.8;
  }
  return audio;
}

function rememberTrack(track) {
  if (!track) {
    return;
  }
  currentTrack = track;
  const index = tracks.findIndex((item) => item.id === track.id);
  if (index >= 0) {
    tracks[index] = track;
  }
  renderTrack();
}

function handleMusicControl(message) {
  rememberTrack(message.track);
  const action = message.action;
  if (musicModeValue() === "server_mix") {
    if (action === "pause") {
      setTrackState("已暂停");
    } else if (action === "stop") {
      setTrackState("已停止");
    } else if (action === "next") {
      setTrackState("下一首");
    } else {
      setTrackState("播放中");
    }
    return;
  }
  if (action === "play") {
    const url = message.track && message.track.url;
    const audio = startSeparateTrack(url);
    if (!audio) {
      return;
    }
    audio.play().catch(() => {
      showError("浏览器拦住了音乐播放，请再点一次播放。");
    });
    setTrackState("播放中");
    return;
  }
  if (action === "pause") {
    if (musicAudio) {
      musicAudio.pause();
    }
    setTrackState("已暂停");
    return;
  }
  if (action === "stop") {
    if (musicAudio) {
      musicAudio.pause();
      musicAudio.currentTime = 0;
    }
    setTrackState("已停止");
    return;
  }
  if (action === "next" || action === "resume") {
    const url = message.track && message.track.url;
    let audio = musicAudio;
    if (!audio || (url && audio.src !== absoluteUrl(url))) {
      audio = startSeparateTrack(url);
    }
    if (audio) {
      audio.play().catch(() => {
        showError("浏览器拦住了音乐播放，请再点一次播放。");
      });
      setTrackState(action === "next" ? "下一首" : "播放中");
    }
  }
}

function clearPlayback() {
  if (!playbackNode) {
    return;
  }
  playbackNode.port.postMessage("clear");
}

function resetSpeaking() {
  userSpeaking = false;
  botSpeaking = false;
  applyMusicVolume();
}

function handleTextMessage(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch (_error) {
    showError("收到无法解析的消息");
    return;
  }
  if (!message || typeof message.type !== "string") {
    return;
  }
  if (message.type === "clear_audio") {
    clearPlayback();
    return;
  }
  if (message.type === "user_speaking") {
    const active = message.active === true;
    if (active) {
      clearPlayback();
    }
    userSpeaking = active;
    applyMusicVolume();
    return;
  }
  if (message.type === "bot_speaking") {
    botSpeaking = message.active === true;
    applyMusicVolume();
    return;
  }
  if (message.type === "music_control") {
    handleMusicControl(message);
    return;
  }
  if (message.type === "asr_interim") {
    asrLive.textContent = message.text || "正在听";
    return;
  }
  if (message.type === "asr_final") {
    asrLive.textContent = "等待你开口";
    appendLog(asrLog, message.text || "");
    return;
  }
  if (message.type === "llm_delta") {
    replyBuffer += message.text || "";
    replyLive.textContent = replyBuffer || "正在回复";
    return;
  }
  if (message.type === "llm_done") {
    appendLog(replyLog, replyBuffer);
    replyBuffer = "";
    replyLive.textContent = "等待回复";
    return;
  }
  if (message.type === "error") {
    showError(message.message || "通话出错");
  }
}

function socketUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return protocol + "//" + location.host + "/ws";
}

async function startCall() {
  if (starting || (socket && socket.readyState === WebSocket.OPEN)) {
    return;
  }
  starting = true;
  clearError();
  setStatus("正在接通", true);
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
    });
    audioContext = new AudioContext();
    await audioContext.audioWorklet.addModule("/static/audio-worklet.js");
    const source = audioContext.createMediaStreamSource(micStream);
    captureNode = new AudioWorkletNode(audioContext, "pcm-capture");
    playbackNode = new AudioWorkletNode(audioContext, "pcm-playback");
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    source.connect(captureNode);
    captureNode.connect(silent);
    silent.connect(audioContext.destination);
    playbackNode.connect(audioContext.destination);
    captureNode.port.onmessage = (event) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(event.data);
      }
    };
    socket = new WebSocket(socketUrl());
    const activeSocket = socket;
    activeSocket.binaryType = "arraybuffer";
    activeSocket.onopen = () => {
      setStatus("通话中", true);
      startButton.disabled = true;
      endButton.disabled = false;
      interruptButton.disabled = false;
      sendConfig();
    };
    activeSocket.onmessage = (event) => {
      if (typeof event.data === "string") {
        handleTextMessage(event.data);
        return;
      }
      if (event.data instanceof ArrayBuffer && playbackNode) {
        playbackNode.port.postMessage(event.data.slice(0));
      }
    };
    activeSocket.onerror = () => {
      showError("通话连接失败");
    };
    activeSocket.onclose = () => {
      if (socket !== activeSocket) {
        return;
      }
      socket = null;
      const updateStatus = stage.classList.contains("is-live") ? undefined : false;
      releaseCallMedia().finally(() => {
        resetCallControls(updateStatus);
      });
    };
    if (audioContext.state === "suspended") {
      await audioContext.resume();
    }
  } catch (error) {
    const denied = error && error.name === "NotAllowedError";
    showError(denied ? "没有拿到麦克风，请允许后再开始通话。" : "没能开始通话，请换用最新版 Chrome 或 Edge。");
    await endCall(false);
    setStatus("未开始", false);
  } finally {
    starting = false;
  }
}

async function releaseCallMedia() {
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  captureNode = null;
  playbackNode = null;
  const context = audioContext;
  audioContext = null;
  if (context && context.state !== "closed") {
    await context.close();
  }
}

function resetCallControls(updateStatus) {
  startButton.disabled = false;
  endButton.disabled = true;
  interruptButton.disabled = true;
  resetSpeaking();
  if (updateStatus !== false) {
    setStatus("已结束", false);
  }
}

async function endCall(updateStatus) {
  const active = socket;
  socket = null;
  if (
    active &&
    active.readyState !== WebSocket.CLOSING &&
    active.readyState !== WebSocket.CLOSED
  ) {
    active.close();
  }
  try {
    await releaseCallMedia();
  } finally {
    resetCallControls(updateStatus);
  }
}

function cycleTrack() {
  if (tracks.length === 0) {
    return null;
  }
  const index = tracks.findIndex((item) => currentTrack && item.id === currentTrack.id);
  currentTrack = tracks[(index + 1) % tracks.length];
  renderTrack();
  return currentTrack;
}

function playCurrent() {
  sendJson({ type: "music_cmd", action: "resume" });
  if (musicModeValue() === "server_mix") {
    setTrackState("已请求播放");
    return;
  }
  if (!currentTrack || !currentTrack.url) {
    showError("曲库里还没有可以播放的曲目");
    return;
  }
  const audio = startSeparateTrack(currentTrack.url);
  if (!audio) {
    return;
  }
  audio.play().catch(() => {
    showError("浏览器拦住了音乐播放，请再点一次播放。");
  });
  setTrackState("播放中");
}

function pauseCurrent() {
  sendJson({ type: "music_cmd", action: "pause" });
  if (musicAudio) {
    musicAudio.pause();
  }
  setTrackState("已暂停");
}

function nextTrack() {
  sendJson({ type: "music_cmd", action: "next" });
  if (musicModeValue() === "server_mix" || !socket || socket.readyState !== WebSocket.OPEN) {
    const track = cycleTrack();
    if (musicModeValue() === "server_mix") {
      setTrackState(track ? "下一首" : "未播放");
      return;
    }
    if (track && track.url) {
      const audio = startSeparateTrack(track.url);
      if (audio) {
        audio.play().catch(() => {
          showError("浏览器拦住了音乐播放，请再点一次播放。");
        });
      }
      setTrackState("下一首");
    }
  }
}

function refreshMusicModeNote() {
  if (musicModeValue() === "server_mix") {
    releaseMusicAudio();
    musicModeNote.textContent = "服务端混音时，音乐已经在语音里，页面不再单独播放。";
    return;
  }
  musicModeNote.textContent = "客户端播放时，音乐会单独出声，说话时音量会降下来。";
}

async function loadVoices() {
  const response = await fetch("/api/voices");
  if (!response.ok) {
    throw new Error("音色列表加载失败");
  }
  const voices = await response.json();
  voiceType.replaceChildren();
  voices.forEach((voice) => {
    const option = document.createElement("option");
    option.value = String(voice.voice_type);
    option.textContent = voice.name + " · " + voice.scene;
    voiceType.appendChild(option);
  });
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
    showError("请先选择一段 WAV");
    return;
  }
  const text = cloneText.value.trim();
  const voiceName = cloneName.value.trim();
  if (!text) {
    showError("请填写这段录音对应的文字");
    return;
  }
  if (!voiceName) {
    showError("请填写音色名称");
    return;
  }
  const form = new FormData();
  form.append("audio", file);
  form.append("text", text);
  form.append("voice_name", voiceName);
  cloneSubmit.disabled = true;
  cloneResult.textContent = "正在复刻";
  try {
    const response = await fetch("/api/clone-voice", { method: "POST", body: form });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : "复刻失败";
      showError(detail);
      cloneResult.textContent = "复刻没有成功";
      return;
    }
    fastVoiceType.value = body.fast_voice_type || "";
    cloneResult.textContent = "复刻完成：" + (body.fast_voice_type || "");
    clearError();
    sendConfig();
  } catch (_error) {
    showError("复刻请求没有发出去");
    cloneResult.textContent = "复刻没有成功";
  } finally {
    cloneSubmit.disabled = false;
  }
}

startButton.addEventListener("click", () => {
  startCall();
});
endButton.addEventListener("click", () => {
  endCall();
});
interruptButton.addEventListener("click", () => {
  sendJson({ type: "interrupt" });
  clearPlayback();
});
playButton.addEventListener("click", playCurrent);
pauseButton.addEventListener("click", pauseCurrent);
nextButton.addEventListener("click", nextTrack);
cloneSubmit.addEventListener("click", () => {
  cloneVoice();
});

[systemPrompt, voiceType, fastVoiceType, speedInput, vadStop, musicMode].forEach((element) => {
  element.addEventListener("input", () => {
    if (element === vadStop) {
      vadStopValue.textContent = Number(vadStop.value).toFixed(1);
    }
    if (element === musicMode) {
      refreshMusicModeNote();
    }
    queueConfig();
  });
  element.addEventListener("change", queueConfig);
});

loadVoices().catch(() => {
  showError("预置音色暂时加载不了");
});
loadTracks().catch(() => {
  showError("曲库暂时加载不了");
});
