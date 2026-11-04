const SAMPLE_RATE = 16000;

function createPcmPlayer(options) {
  const hooks = options || {};
  let audioContext = null;
  let nextStartTime = 0;
  let errorText = "";
  const sources = [];

  function recordError(message) {
    errorText = message;
    if (typeof hooks.onError === "function") {
      hooks.onError(message);
    }
  }

  function ensureContext() {
    if (audioContext) {
      return audioContext;
    }
    if (typeof wx === "undefined" || typeof wx.createWebAudioContext !== "function") {
      return null;
    }
    audioContext = wx.createWebAudioContext();
    nextStartTime = audioContext.currentTime || 0;
    return audioContext;
  }

  function forget(source) {
    const index = sources.indexOf(source);
    if (index >= 0) {
      sources.splice(index, 1);
    }
  }

  function push(arrayBuffer) {
    try {
      const context = ensureContext();
      if (!context) {
        recordError("当前基础库没有 WebAudio，语音播不出来。");
        return;
      }
      if (!arrayBuffer || typeof arrayBuffer.byteLength !== "number") {
        return;
      }
      const evenLength = arrayBuffer.byteLength - (arrayBuffer.byteLength % 2);
      if (evenLength < 2) {
        return;
      }
      const pcm = new Int16Array(arrayBuffer.slice(0, evenLength));
      const samples = pcm.length;
      const audioBuffer = context.createBuffer(1, samples, 16000);
      const channel = audioBuffer.getChannelData(0);
      for (let index = 0; index < samples; index += 1) {
        const sample = pcm[index];
        channel[index] = sample < 0 ? sample / 32768 : sample / 32767;
      }
      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(context.destination);
      if (context.state === "suspended" && typeof context.resume === "function") {
        const pending = context.resume();
        if (pending && typeof pending.catch === "function") {
          pending.catch(function () {});
        }
      }
      if (nextStartTime < context.currentTime) {
        nextStartTime = context.currentTime;
      }
      source.start(nextStartTime);
      nextStartTime += audioBuffer.duration || samples / SAMPLE_RATE;
      sources.push(source);
      source.onended = function () {
        forget(source);
      };
    } catch (error) {
      recordError("语音播放失败");
    }
  }

  function clear() {
    sources.splice(0).forEach(function (source) {
      try {
        source.stop();
      } catch (error) {
        // 已经播完的节点再 stop 会抛错，清队列时忽略。
      }
      try {
        source.disconnect();
      } catch (error) {
        // 断开失败不影响下一次排队。
      }
    });
    if (!audioContext) {
      nextStartTime = 0;
      return;
    }
    nextStartTime = audioContext.currentTime;
  }

  return {
    push: push,
    clear: clear,
    get error() {
      return errorText;
    },
  };
}

module.exports = {
  createPcmPlayer: createPcmPlayer,
};
