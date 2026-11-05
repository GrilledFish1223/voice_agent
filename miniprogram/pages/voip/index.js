const { createPcmPlayer } = require("./pcm-player");

const DEFAULT_SERVER = "ws://127.0.0.1:8765/ws";
const RECORD_LIMIT_MS = 600000;
const RESTART_BEFORE_MS = 20000;
const DEFAULT_VOLUME = 0.8;
const DUCK_RELEASE_MS = 800;
const DEFAULT_PROMPT =
  "你是一个专业热情的智能语音助手，可以陪聊和播放音乐。回答使用口语，每次不超过两句话。用户要求放歌、暂停、切歌或调节音量时，调用音乐工具。";

const VOICES = [
  { voice_type: 602003, name: "默认音色", scene: "超自然大模型音色" },
  { voice_type: 502007, name: "智小虎", scene: "聊天童声" },
  { voice_type: 502006, name: "智小悟", scene: "聊天男声" },
  { voice_type: 502005, name: "智小解", scene: "解说男声" },
  { voice_type: 502004, name: "智小满", scene: "营销女声" },
  { voice_type: 502003, name: "智小敏", scene: "聊天女声" },
  { voice_type: 502001, name: "智小柔", scene: "聊天女声" },
  { voice_type: 602004, name: "暖心阿灿", scene: "聊天男声" },
  { voice_type: 602005, name: "专业梓欣", scene: "聊天女声" },
  { voice_type: 603000, name: "懂事少年", scene: "特色男声" },
  { voice_type: 603001, name: "潇湘妹妹", scene: "特色女声" },
  { voice_type: 603002, name: "软萌心心", scene: "特色男童声" },
  { voice_type: 603003, name: "随和老李", scene: "聊天男声" },
];

const SEED_TRACKS = [
  { id: "morning_breeze", title: "晨光微风", artist: "LaunchKit Synth", url: "/api/music/stream/morning_breeze" },
  { id: "deep_sea", title: "深海冥想", artist: "LaunchKit Synth", url: "/api/music/stream/deep_sea" },
  { id: "cyber_pulse", title: "赛博律动", artist: "LaunchKit Synth", url: "/api/music/stream/cyber_pulse" },
];

function voiceLabel(voice) {
  return voice.name + " · " + voice.scene;
}

Page({
  data: {
    serverUrl: DEFAULT_SERVER,
    callState: "未开始",
    inCall: false,
    asrLive: "等待你开口",
    replyLive: "等待回复",
    asrLog: [],
    replyLog: [],
    errorText: "",
    muted: false,
    speakerOn: true,
    speaking: "",
    voiceNames: VOICES.map(voiceLabel),
    voiceIndex: 0,
    musicModeNames: ["客户端独立播放", "服务端混音"],
    musicModeIndex: 0,
    musicMode: "client_url",
    musicNote: "播放时进入音乐模式并暂停拾音，点暂停回到对话。",
    sessionMode: "conversation",
    songQuery: "",
    trackTitle: "还没有曲目",
    trackArtist: "",
    trackState: "未播放",
  },

  onLoad() {
    this.player = createPcmPlayer({
      onError: (message) => {
        this.showError(message);
      },
    });
    this.socketTask = null;
    this.socketOpen = false;
    this.callActive = false;
    this.starting = false;
    this.ending = false;
    this.recording = false;
    this.userSpeaking = false;
    this.botSpeaking = false;
    this.restingVolume = DEFAULT_VOLUME;
    this.replyBuffer = "";
    this.musicAudio = null;
    this.tracks = [];
    this.currentTrack = null;
    this.duckTimer = null;
    this.restartTimer = null;
    this.lastRecordStart = 0;
    this.recorder = wx.getRecorderManager();
    this.bindRecorder();
    this.loadTracks();
  },

  onUnload() {
    this.endCall(false);
  },

  bindRecorder() {
    const recorder = this.recorder;
    recorder.onFrameRecorded((res) => {
      const frameBuffer = res && res.frameBuffer;
      if (!frameBuffer || !this.socketOpen || this.data.muted || !this.socketTask || this.data.sessionMode === "music") {
        return;
      }
      this.socketTask.send({ data: frameBuffer });
    });
    recorder.onStop(() => {
      this.recording = false;
      if (!this.callActive) {
        return;
      }
      if (Date.now() - this.lastRecordStart < 1000) {
        this.showError("录音被系统中断，请重新开始通话。");
        this.endCall(true);
        return;
      }
      this.startRecording();
    });
    recorder.onError(() => {
      this.showError("没有拿到麦克风，请允许后再开始通话。");
      if (!this.socketOpen) {
        this.endCall(true);
      }
    });
  },

  startRecording() {
    this.clearRestartTimer();
    this.recording = true;
    this.lastRecordStart = Date.now();
    this.recorder.start({
      duration: 600000,
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
      format: "pcm",
      frameSize: 2
    });
    // 单次录音最长 600000ms，提前重开，避免通话被系统截断。
    this.restartTimer = setTimeout(() => {
      if (!this.callActive || !this.recording) {
        return;
      }
      this.recorder.stop();
    }, RECORD_LIMIT_MS - RESTART_BEFORE_MS);
  },

  stopRecording() {
    this.clearRestartTimer();
    if (!this.recording) {
      return;
    }
    this.recording = false;
    this.recorder.stop();
  },

  clearRestartTimer() {
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
  },

  showError(text) {
    this.setData({ errorText: text || "通话出错" });
  },

  clearError() {
    if (this.data.errorText) {
      this.setData({ errorText: "" });
    }
  },

  sendJson(payload) {
    if (!this.socketOpen || !this.socketTask) {
      return;
    }
    this.socketTask.send({ data: JSON.stringify(payload) });
  },

  buildConfig() {
    const voice = VOICES[this.data.voiceIndex] || VOICES[0];
    const musicMode = this.data.musicMode === "server_mix" ? "server_mix" : "client_url";
    return {
      type: "config",
      system_prompt: DEFAULT_PROMPT,
      voice_type: Number(voice.voice_type || 602003),
      fast_voice_type: "",
      speed: 0,
      volume: 0,
      vad_stop_secs: 0.2,
      vad_confidence: 0.7,
      llm_model: "",
      music_mode: musicMode,
    };
  },

  sendConfig() {
    this.sendJson(this.buildConfig());
  },

  httpOrigin(wsUrl) {
    const matched = /^wss?:\/\/([^/?#]+)/i.exec(String(wsUrl || "").trim());
    if (!matched) {
      return "";
    }
    const secure = /^wss:\/\//i.test(String(wsUrl || ""));
    return (secure ? "https://" : "http://") + matched[1];
  },

  absoluteMediaUrl(url) {
    if (!url) {
      return "";
    }
    if (/^https?:\/\//i.test(url)) {
      return url;
    }
    const origin = this.httpOrigin(this.data.serverUrl);
    if (url.charAt(0) === "/") {
      return origin + url;
    }
    return origin + "/" + url;
  },

  loadTracks() {
    const origin = this.httpOrigin(this.data.serverUrl);
    if (!origin) {
      this.tracks = SEED_TRACKS.slice();
      return;
    }
    wx.request({
      url: origin + "/api/music",
      success: (res) => {
        if (!res || res.statusCode !== 200 || !Array.isArray(res.data) || res.data.length === 0) {
          this.tracks = SEED_TRACKS.slice();
          return;
        }
        this.tracks = res.data;
        if (!this.currentTrack) {
          this.rememberTrack(res.data[0]);
        }
      },
      fail: () => {
        this.tracks = SEED_TRACKS.slice();
      },
    });
  },

  onServerInput(event) {
    this.setData({ serverUrl: event.detail.value });
  },

  onSongInput(event) {
    this.setData({ songQuery: event.detail.value });
  },

  onVoiceChange(event) {
    this.setData({ voiceIndex: Number(event.detail.value) });
    this.sendConfig();
  },

  onMusicModeChange(event) {
    const musicModeIndex = Number(event.detail.value);
    const musicMode = musicModeIndex === 1 ? "server_mix" : "client_url";
    this.setData({
      musicModeIndex: musicModeIndex,
      musicMode: musicMode,
      musicNote: musicMode === "server_mix"
        ? "服务端混音 · 播放时关拾音，暂停后回到对话。"
        : "播放时进入音乐模式并暂停拾音，点暂停回到对话。",
    });
    if (musicMode === "server_mix") {
      this.releaseMusicAudio();
    }
    this.sendConfig();
  },

  applySpeakerRoute() {
    if (typeof wx.setInnerAudioOption !== "function") {
      return;
    }
    wx.setInnerAudioOption({
      speakerOn: this.data.speakerOn,
      mixWithOther: true,
    });
  },

  toggleMute() {
    this.setData({ muted: !this.data.muted });
  },

  toggleSpeaker() {
    this.setData({ speakerOn: !this.data.speakerOn });
    this.applySpeakerRoute();
  },

  startCall() {
    if (this.starting || this.callActive) {
      return;
    }
    const url = String(this.data.serverUrl || "").trim();
    if (!/^wss?:\/\//i.test(url)) {
      this.showError("服务器地址需要以 ws:// 或 wss:// 开头");
      return;
    }
    this.starting = true;
    this.clearError();
    this.setData({ serverUrl: url, callState: "正在接通", inCall: true });
    this.applySpeakerRoute();
    this.callActive = true;
    this.startRecording();
    const task = wx.connectSocket({
      url: url,
      fail: () => {
        this.showError("通话连接失败");
        this.endCall(true);
      },
    });
    if (!task || typeof task.onOpen !== "function") {
      this.showError("通话连接失败");
      this.endCall(true);
      return;
    }
    this.socketTask = task;
    this.bindSocket(task);
  },

  bindSocket(task) {
    task.onOpen(() => {
      if (this.socketTask !== task) {
        return;
      }
      this.socketOpen = true;
      this.starting = false;
      this.setData({ callState: "通话中", inCall: true });
      this.sendConfig();
    });
    task.onMessage((res) => {
      if (this.socketTask !== task) {
        return;
      }
      const data = res && res.data;
      if (typeof data === "string") {
        this.handleTextMessage(data);
        return;
      }
      if (data instanceof ArrayBuffer) {
        this.player.push(data);
      }
    });
    task.onError(() => {
      if (this.socketTask !== task) {
        return;
      }
      this.showError("通话连接失败");
    });
    task.onClose(() => {
      if (this.socketTask !== task) {
        return;
      }
      this.socketTask = null;
      this.socketOpen = false;
      this.starting = false;
      this.callActive = false;
      this.stopRecording();
      this.player.clear();
      this.releaseMusicAudio();
      this.currentTrack = null;
      this.resetSpeaking();
      this.setData({ callState: "已结束", inCall: false, sessionMode: "conversation", trackTitle: "还没有曲目" });
    });
  },

  endCall(updateStatus) {
    if (this.ending) {
      return;
    }
    this.ending = true;
    const wasLive = this.callActive || this.socketOpen || this.recording || this.data.inCall;
    this.callActive = false;
    this.starting = false;
    this.socketOpen = false;
    this.stopRecording();
    const task = this.socketTask;
    this.socketTask = null;
    if (task && typeof task.close === "function") {
      task.close({});
    }
    if (this.player) {
      this.player.clear();
    }
    this.releaseMusicAudio();
    this.currentTrack = null;
    this.setData({ trackTitle: "还没有曲目", sessionMode: "conversation" });
    this.resetSpeaking();
    if (updateStatus !== false && wasLive) {
      this.setData({ callState: "已结束", inCall: false });
    }
    this.ending = false;
  },

  interruptCall() {
    this.sendJson({ type: "interrupt" });
    this.player.clear();
  },

  appendLog(field, text) {
    const line = String(text || "").trim();
    if (!line) {
      return;
    }
    const next = (this.data[field] || []).concat(line).slice(-8);
    this.setData({ [field]: next });
  },

  handleTextMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch (error) {
      this.showError("收到无法解析的消息");
      return;
    }
    if (!message || typeof message.type !== "string") {
      return;
    }
    if (message.type === "clear_audio") {
      this.player.clear();
      return;
    }
    if (message.type === "user_speaking") {
      const active = message.active === true;
      if (active) {
        this.player.clear();
      }
      this.userSpeaking = active;
      this.syncSpeaking();
      return;
    }
    if (message.type === "bot_speaking") {
      this.botSpeaking = message.active === true;
      this.syncSpeaking();
      return;
    }
    if (message.type === "session_mode") {
      this.applySessionMode(message.mode);
      return;
    }
    if (message.type === "music_control") {
      this.handleMusicControl(message);
      return;
    }
    if (message.type === "asr_interim") {
      this.setData({ asrLive: message.text || "正在听" });
      return;
    }
    if (message.type === "asr_final") {
      this.setData({ asrLive: "等待你开口" });
      this.appendLog("asrLog", message.text || "");
      return;
    }
    if (message.type === "llm_delta") {
      this.replyBuffer += message.text || "";
      this.setData({ replyLive: this.replyBuffer || "正在回复" });
      return;
    }
    if (message.type === "llm_done") {
      this.appendLog("replyLog", this.replyBuffer);
      this.replyBuffer = "";
      this.setData({ replyLive: "等待回复" });
      return;
    }
    if (message.type === "error") {
      this.showError(message.message || "通话出错");
    }
  },

  syncSpeaking() {
    let speaking = "";
    if (this.botSpeaking) {
      speaking = "bot";
    }
    if (this.userSpeaking) {
      speaking = "user";
    }
    if (speaking !== this.data.speaking) {
      this.setData({ speaking: speaking });
    }
    this.applyMusicVolume();
  },

  resetSpeaking() {
    this.userSpeaking = false;
    this.botSpeaking = false;
    this.syncSpeaking();
  },

  ensureMusicAudio() {
    if (this.data.musicMode === "server_mix") {
      return null;
    }
    if (this.musicAudio) {
      return this.musicAudio;
    }
    if (typeof wx.createInnerAudioContext !== "function") {
      this.showError("当前基础库没有音乐播放器。");
      return null;
    }
    const audio = wx.createInnerAudioContext();
    audio.loop = true;
    audio.obeyMuteSwitch = false;
    audio.onError(() => {
      this.showError("音乐没有播出来，请再点一次点歌。");
    });
    this.musicAudio = audio;
    return audio;
  },

  releaseMusicAudio() {
    if (this.duckTimer) {
      clearTimeout(this.duckTimer);
      this.duckTimer = null;
    }
    const audio = this.musicAudio;
    this.musicAudio = null;
    if (!audio) {
      return;
    }
    audio.stop();
    if (typeof audio.destroy === "function") {
      audio.destroy();
    }
  },

  readRestingVolume(message) {
    if (!message || message.volume === undefined || message.volume === null || message.volume === "") {
      return;
    }
    const next = Number(message.volume);
    if (!Number.isFinite(next)) {
      return;
    }
    this.restingVolume = Math.min(1, Math.max(0, next));
  },

  currentMusicVolume() {
    const resting = Number.isFinite(this.restingVolume) ? this.restingVolume : DEFAULT_VOLUME;
    if (this.userSpeaking || this.botSpeaking) {
      return resting * 0.25;
    }
    return resting;
  },

  applyMusicVolume() {
    if (this.data.musicMode === "server_mix") {
      return;
    }
    const audio = this.musicAudio;
    if (!audio) {
      return;
    }
    if (this.userSpeaking || this.botSpeaking) {
      if (this.duckTimer) {
        clearTimeout(this.duckTimer);
        this.duckTimer = null;
      }
      audio.volume = this.currentMusicVolume();
      return;
    }
    if (this.duckTimer) {
      clearTimeout(this.duckTimer);
    }
    this.duckTimer = setTimeout(() => {
      if (this.userSpeaking || this.botSpeaking || !this.musicAudio) {
        return;
      }
      if (this.data.musicMode === "server_mix") {
        return;
      }
      const resting = Number.isFinite(this.restingVolume) ? this.restingVolume : DEFAULT_VOLUME;
      this.musicAudio.volume = resting;
    }, DUCK_RELEASE_MS);
  },

  startSeparateTrack(url) {
    if (this.data.musicMode === "server_mix" || !url) {
      return null;
    }
    const audio = this.ensureMusicAudio();
    if (!audio) {
      return null;
    }
    const absolute = this.absoluteMediaUrl(url);
    if (audio.src !== absolute) {
      audio.stop();
      audio.src = absolute;
    }
    audio.loop = true;
    audio.volume = this.currentMusicVolume();
    return audio;
  },

  rememberTrack(track) {
    if (!track) {
      return;
    }
    this.currentTrack = track;
    const index = this.tracks.findIndex((item) => item.id === track.id);
    if (index >= 0) {
      this.tracks[index] = track;
    }
    this.setData({
      trackTitle: track.title || "还没有曲目",
      trackArtist: track.artist || "",
    });
  },

  applySessionMode(mode) {
    const next = mode === "music" ? "music" : "conversation";
    if (this.data.sessionMode === next) {
      return;
    }
    this.setData({ sessionMode: next });
  },

  handleMusicControl(message) {
    if (message.session_mode) {
      this.applySessionMode(message.session_mode);
    } else if (message.action === "pause" || message.action === "stop") {
      this.applySessionMode("conversation");
    } else if (message.action === "play" || message.action === "resume" || message.action === "next") {
      this.applySessionMode("music");
    }
    this.rememberTrack(message.track);
    this.readRestingVolume(message);
    const action = message.action;
    if (this.data.musicMode === "server_mix") {
      if (action === "pause") {
        this.setData({ trackState: "已暂停" });
      } else if (action === "stop") {
        this.setData({ trackState: "已停止" });
      } else if (action === "next") {
        this.setData({ trackState: "下一首" });
      } else {
        this.setData({ trackState: "播放中" });
      }
      return;
    }
    if (action === "play") {
      const url = message.track && message.track.url;
      const audio = this.startSeparateTrack(url);
      if (audio) {
        audio.play();
        this.setData({ trackState: "播放中" });
      }
    } else if (action === "pause") {
      if (this.musicAudio) {
        this.musicAudio.pause();
      }
      this.setData({ trackState: "已暂停" });
    } else if (action === "stop") {
      if (this.musicAudio) {
        this.musicAudio.pause();
        this.musicAudio.seek(0);
      }
      this.setData({ trackState: "已停止" });
    } else if (action === "next" || action === "resume") {
      const url = message.track && message.track.url;
      let audio = this.musicAudio;
      const absolute = url ? this.absoluteMediaUrl(url) : "";
      if (!audio || (absolute && audio.src !== absolute)) {
        audio = this.startSeparateTrack(url);
      }
      if (audio) {
        audio.play();
        this.setData({ trackState: action === "next" ? "下一首" : "播放中" });
      }
    }
    this.applyMusicVolume();
  },

  trackList() {
    return this.tracks.length ? this.tracks : SEED_TRACKS;
  },

  findTrack(query) {
    const tracks = this.trackList();
    const needle = String(query || "").trim();
    if (!needle) {
      return this.currentTrack || tracks[0] || null;
    }
    for (let index = 0; index < tracks.length; index += 1) {
      const track = tracks[index];
      const title = track.title || "";
      const artist = track.artist || "";
      const id = track.id || "";
      if (title.indexOf(needle) >= 0 || artist.indexOf(needle) >= 0 || id.indexOf(needle) >= 0) {
        return track;
      }
    }
    return null;
  },

  requestSong() {
    const query = String(this.data.songQuery || "").trim();
    if (this.data.musicMode === "server_mix") {
      this.setData({
        trackState: query ? "请直接说出歌名" : "请直接说出想听的歌",
      });
      return;
    }
    const track = this.findTrack(query);
    if (!track || !track.url) {
      this.showError(query ? "曲库里没有这首" : "曲库里还没有可以播放的曲目");
      return;
    }
    this.rememberTrack(track);
    this.applySessionMode("music");
    this.sendJson({ type: "music_cmd", action: "resume" });
    const audio = this.startSeparateTrack(track.url);
    if (!audio) {
      return;
    }
    audio.play();
    this.setData({ trackState: "播放中" });
  },

  pauseSong() {
    this.applySessionMode("conversation");
    this.sendJson({ type: "music_cmd", action: "pause" });
    if (this.data.musicMode !== "server_mix" && this.musicAudio) {
      this.musicAudio.pause();
    }
    this.setData({ trackState: "已暂停" });
  },

  cycleTrack() {
    const tracks = this.trackList();
    if (!tracks.length) {
      return null;
    }
    const index = tracks.findIndex((item) => this.currentTrack && item.id === this.currentTrack.id);
    const track = tracks[(index + 1) % tracks.length];
    this.rememberTrack(track);
    return track;
  },

  nextSong() {
    this.applySessionMode("music");
    this.sendJson({ type: "music_cmd", action: "next" });
    if (this.data.musicMode === "server_mix" || !this.socketOpen) {
      const track = this.cycleTrack();
      if (this.data.musicMode === "server_mix") {
        this.setData({ trackState: track ? "下一首" : "未播放" });
        return;
      }
      if (track && track.url) {
        const audio = this.startSeparateTrack(track.url);
        if (audio) {
          audio.play();
        }
        this.setData({ trackState: "下一首" });
      }
    }
  },
});
