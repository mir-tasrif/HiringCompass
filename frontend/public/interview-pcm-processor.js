class InterviewPcmProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.inputRate = options.processorOptions.inputRate;
    this.phase = 0;
    this.samples = [];
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      this.phase += 16000;
      if (this.phase >= this.inputRate) {
        this.phase -= this.inputRate;
        this.samples.push(Math.max(-32768, Math.min(32767, Math.round(sample * 32767))));
      }
      if (this.samples.length >= 1600) {
        const pcm = new Int16Array(this.samples.splice(0, 1600));
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
      }
    }
    return true;
  }
}

registerProcessor("interview-pcm-processor", InterviewPcmProcessor);
