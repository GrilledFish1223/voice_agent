const TARGET_RATE = 16000;
const CAPTURE_FRAME_SAMPLES = 640; // 40ms @ 16kHz (1280 bytes), 避免每 2.6ms 触发一次消息风暴

function clampToInt16(sample) {
  const clamped = Math.max(-1, Math.min(1, sample));
  const scaled = clamped < 0 ? clamped * 32768 : clamped * 32767;
  return Math.max(-32768, Math.min(32767, Math.round(scaled)));
}

function int16ToFloat(sample) {
  return sample < 0 ? sample / 32768 : sample / 32767;
}

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.cursor = 0;
    this.chunkBuffer = new Int16Array(CAPTURE_FRAME_SAMPLES);
    this.chunkOffset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) {
      return true;
    }
    const step = sampleRate / TARGET_RATE;
    let cursor = this.cursor;
    while (cursor < channel.length) {
      const index = Math.floor(cursor);
      const fraction = cursor - index;
      const next = Math.min(index + 1, channel.length - 1);
      const mixed = channel[index] + (channel[next] - channel[index]) * fraction;
      this.chunkBuffer[this.chunkOffset] = clampToInt16(mixed);
      this.chunkOffset += 1;
      if (this.chunkOffset >= CAPTURE_FRAME_SAMPLES) {
        const out = this.chunkBuffer.slice(0, CAPTURE_FRAME_SAMPLES);
        this.port.postMessage(out.buffer, [out.buffer]);
        this.chunkOffset = 0;
      }
      cursor += step;
    }
    this.cursor = cursor - channel.length;
    return true;
  }
}

class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // 45 秒环形缓冲区，防止长回复溢出覆盖未播放音频导致卡顿
    this.capacity = TARGET_RATE * 45;
    this.buffer = new Int16Array(this.capacity);
    this.readPointer = 0;
    this.writePointer = 0;
    this.fraction = 0;
    this.port.onmessage = (event) => {
      const data = event.data;
      if (data === "clear" || (data && data.type === "clear")) {
        this.readPointer = this.writePointer;
        this.fraction = 0;
        return;
      }
      if (!(data instanceof ArrayBuffer) || data.byteLength < 2) {
        return;
      }
      const samples = new Int16Array(data);
      for (let index = 0; index < samples.length; index += 1) {
        this.enqueue(samples[index]);
      }
    };
  }

  enqueue(sample) {
    const next = (this.writePointer + 1) % this.capacity;
    if (next === this.readPointer) {
      this.readPointer = (this.readPointer + 1) % this.capacity;
    }
    this.buffer[this.writePointer] = sample;
    this.writePointer = next;
  }

  readSample(step) {
    if (this.readPointer === this.writePointer) {
      return 0;
    }
    const current = int16ToFloat(this.buffer[this.readPointer]);
    const nextIndex = (this.readPointer + 1) % this.capacity;
    const next = nextIndex === this.writePointer ? current : int16ToFloat(this.buffer[nextIndex]);
    const mixed = current + (next - current) * this.fraction;
    this.fraction += step;
    while (this.fraction >= 1) {
      this.fraction -= 1;
      if (this.readPointer === this.writePointer) {
        this.fraction = 0;
        break;
      }
      this.readPointer = (this.readPointer + 1) % this.capacity;
    }
    return mixed;
  }

  process(_inputs, outputs) {
    const channel = outputs[0] && outputs[0][0];
    if (!channel) {
      return true;
    }
    const step = TARGET_RATE / sampleRate;
    for (let index = 0; index < channel.length; index += 1) {
      channel[index] = this.readSample(step);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
registerProcessor("pcm-playback", PcmPlaybackProcessor);
