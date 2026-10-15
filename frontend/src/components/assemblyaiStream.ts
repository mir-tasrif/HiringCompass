export interface AssemblyStream { stop: () => Promise<void> }

export async function startAssemblyStream(audio: MediaStreamTrack, token: string,
  onFailure: () => void): Promise<AssemblyStream> {
  if (!window.AudioWorkletNode) throw new Error("This browser cannot stream microphone audio.");
  const context = new AudioContext();
  let socket: WebSocket | null = null;
  let stopped = false;
  let source: MediaStreamAudioSourceNode | null = null;
  let worklet: AudioWorkletNode | null = null;
  let silent: GainNode | null = null;
  try {
    await context.audioWorklet.addModule("/interview-pcm-processor.js");
    await context.resume();
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/api/interviews/stt`);
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => reject(new Error("Transcription connection timed out.")), 15000);
      socket!.onopen = () => socket!.send(JSON.stringify({ token }));
      socket!.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "ready") { window.clearTimeout(timeout); resolve(); }
          else if (message.type === "error") { window.clearTimeout(timeout); reject(new Error(message.message)); }
        } catch { /* Ignore unrecognized control messages. */ }
      };
      socket!.onerror = () => { window.clearTimeout(timeout); reject(new Error("Transcription connection failed.")); };
      socket!.onclose = () => { window.clearTimeout(timeout); reject(new Error("Transcription connection closed.")); };
    });
    if (audio.readyState !== "live") throw new Error("Microphone track is unavailable.");
    source = context.createMediaStreamSource(new MediaStream([audio]));
    worklet = new AudioWorkletNode(context, "interview-pcm-processor", {
      processorOptions: { inputRate: context.sampleRate },
    });
    silent = context.createGain();
    silent.gain.value = 0;
    source.connect(worklet);
    worklet.connect(silent);
    silent.connect(context.destination);
    worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (!stopped && socket?.readyState === WebSocket.OPEN) {
        if (socket.bufferedAmount < 512_000) socket.send(event.data);
        else onFailure();
      }
    };
    socket.onclose = () => { if (!stopped) onFailure(); };
    socket.onerror = () => { if (!stopped) onFailure(); };
    return { stop: async () => {
      if (stopped) return;
      stopped = true;
      source?.disconnect(); worklet?.disconnect(); silent?.disconnect();
      await context.close();
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "stop" }));
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 4000);
          socket!.addEventListener("close", () => { window.clearTimeout(timer); resolve(); }, { once: true });
        });
      }
      socket?.close();
    } };
  } catch (error) {
    stopped = true;
    socket?.close();
    source?.disconnect(); worklet?.disconnect(); silent?.disconnect();
    await context.close();
    throw error;
  }
}
