// LiveKit room and microphone lifecycle; sends audio to the configured transcription provider.
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import { JoinCredentials, sendAudioSegment } from "../api/interviews";
import { AssemblyStream, startAssemblyStream } from "./assemblyaiStream";

type PendingAudio = { id: string; start: string; end: string; blob: Blob };

interface Props {
  credentials: JoinCredentials;
  identity: string;
  role: "candidate" | "interviewer";
  onEnd?: () => Promise<void>;
  onLeave: () => void;
  onCallStateChange?: (state: { connection: string; mic: boolean; camera: boolean; ending: boolean }) => void;
}

export interface InterviewCallHandle {
  toggleMic: () => Promise<void>;
  toggleCamera: () => Promise<void>;
  leave: () => Promise<void>;
  end: () => Promise<void>;
}

const InterviewCall = forwardRef<InterviewCallHandle, Props>(function InterviewCall(
  { credentials, identity, role, onEnd, onLeave, onCallStateChange }: Props, ref) {
  const roomRef = useRef<Room | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingRequestedRef = useRef(false);
  const segmentTimerRef = useRef<number | null>(null);
  const activityTimerRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const pendingRef = useRef<PendingAudio[]>([]);
  const uploadingRef = useRef(false);
  const assemblyRef = useRef<AssemblyStream | null>(null);
  const assemblyStartingRef = useRef(false);
  const microphoneRef = useRef<MediaStreamTrack | null>(null);
  const localRef = useRef<HTMLDivElement>(null);
  const remoteRef = useRef<HTMLDivElement>(null);
  const remoteAudioRef = useRef<HTMLDivElement>(null);
  const [connection, setConnection] = useState("Connecting…");
  const [mic, setMic] = useState(true);
  const [camera, setCamera] = useState(true);
  const [transcriptionWarning, setTranscriptionWarning] = useState(false);
  const [ending, setEnding] = useState(false);

  useEffect(() => {
    onCallStateChange?.({ connection, mic, camera, ending });
  }, [connection, mic, camera, ending, onCallStateChange]);

  const flush = async () => {
    if (uploadingRef.current) return;
    uploadingRef.current = true;
    try {
      while (pendingRef.current.length) {
        const item = pendingRef.current[0];
        try {
          await sendAudioSegment(role, identity, item.id, item.start, item.end, item.blob);
          pendingRef.current.shift();
          setTranscriptionWarning(false);
        } catch {
          setTranscriptionWarning(true);
          break;
        }
      }
    } finally { uploadingRef.current = false; }
  };

  const beginRecording = (audio?: MediaStreamTrack) => {
    if (!audio || typeof MediaRecorder === "undefined") { setTranscriptionWarning(true); return; }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find((type) => MediaRecorder.isTypeSupported(type));
    if (!mime) { setTranscriptionWarning(true); return; }
    recordingRequestedRef.current = true;
    const audioContext = new AudioContext();
    audioContextRef.current = audioContext;
    const source = audioContext.createMediaStreamSource(new MediaStream([audio]));
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const levels = new Uint8Array(analyser.fftSize);
    let speechFrames = 0;
    void audioContext.resume();
    activityTimerRef.current = window.setInterval(() => {
      analyser.getByteTimeDomainData(levels);
      const energy = levels.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / levels.length;
      if (Math.sqrt(energy) >= 0.012) speechFrames += 1;
    }, 100);
    const recordSegment = () => {
      if (!recordingRequestedRef.current || audio.readyState !== "live") return;
    speechFrames = 0;
    const recorder = new MediaRecorder(new MediaStream([audio]), { mimeType: mime });
    recorderRef.current = recorder;
    const started = new Date().toISOString();
    const pieces: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) pieces.push(event.data);
    };
    recorder.onstop = () => {
      if (pieces.length && speechFrames >= 3) {
        pendingRef.current.push({ id: crypto.randomUUID(), start: started,
          end: new Date().toISOString(), blob: new Blob(pieces, { type: mime }) });
        if (pendingRef.current.length > 30) { pendingRef.current.shift(); setTranscriptionWarning(true); }
        void flush();
      }
      if (recordingRequestedRef.current) recordSegment();
    };
    recorder.start();
    segmentTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, 6000);
    };
    recordSegment();
  };

  const stopRecording = () => {
    recordingRequestedRef.current = false;
    if (segmentTimerRef.current !== null) window.clearTimeout(segmentTimerRef.current);
    segmentTimerRef.current = null;
    if (activityTimerRef.current !== null) window.clearInterval(activityTimerRef.current);
    activityTimerRef.current = null;
    if (audioContextRef.current) void audioContextRef.current.close();
    audioContextRef.current = null;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  };

  const beginTranscription = (audio?: MediaStreamTrack) => {
    microphoneRef.current = audio ?? null;
    if (credentials.stt_provider !== "assemblyai") { beginRecording(audio); return; }
    if (!audio || !credentials.stt_token) { setTranscriptionWarning(true); return; }
    recordingRequestedRef.current = true;
    if (assemblyRef.current || assemblyStartingRef.current) return;
    assemblyStartingRef.current = true;
    void startAssemblyStream(audio, credentials.stt_token, () => {
      setTranscriptionWarning(true);
      const stream = assemblyRef.current;
      assemblyRef.current = null;
      if (stream) void stream.stop();
    }).then((stream) => {
      if (!recordingRequestedRef.current) void stream.stop();
      else { assemblyRef.current = stream; setTranscriptionWarning(false); }
    }).catch(() => setTranscriptionWarning(true))
      .finally(() => { assemblyStartingRef.current = false; });
  };

  const stopTranscription = async () => {
    if (credentials.stt_provider !== "assemblyai") { stopRecording(); return; }
    recordingRequestedRef.current = false;
    microphoneRef.current = null;
    const stream = assemblyRef.current;
    assemblyRef.current = null;
    if (stream) await stream.stop();
  };

  useEffect(() => {
    let closed = false;
    const room = new Room({ adaptiveStream: true, dynacast: true });
    roomRef.current = room;
    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Audio) {
        const element = track.attach();
        if (track.kind === Track.Kind.Video) remoteRef.current?.replaceChildren(element);
        else remoteAudioRef.current?.appendChild(element);
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((element) => element.remove()));
    room.on(RoomEvent.Reconnecting, () => setConnection("Network interrupted · reconnecting…"));
    room.on(RoomEvent.Reconnected, () => { setConnection("Connected"); void flush(); });
    room.on(RoomEvent.Disconnected, () => {
      void stopTranscription();
      if (!closed) setConnection("Disconnected · rejoin to continue");
    });

    const connect = async () => {
      try {
        await room.connect(credentials.url, credentials.token);
        if (closed) { room.disconnect(); return; }
        await room.localParticipant.enableCameraAndMicrophone();
        const video = room.localParticipant.getTrackPublication(Track.Source.Camera)?.videoTrack;
        if (video) localRef.current?.replaceChildren(video.attach());
        const audio = room.localParticipant.getTrackPublication(Track.Source.Microphone)?.audioTrack?.mediaStreamTrack;
        beginTranscription(audio);
        setConnection("Connected");
      } catch (error) {
        setConnection(error instanceof Error ? error.message : "Could not connect to the interview.");
      }
    };
    void connect();
    const timer = window.setInterval(() => {
      if (credentials.stt_provider === "assemblyai") {
        if (recordingRequestedRef.current && !assemblyRef.current && !assemblyStartingRef.current)
          beginTranscription(microphoneRef.current ?? undefined);
      } else void flush();
    }, 3000);
    return () => {
      closed = true;
      window.clearInterval(timer);
      void stopTranscription();
      room.disconnect();
      roomRef.current = null;
      localRef.current?.replaceChildren();
      remoteRef.current?.replaceChildren();
      remoteAudioRef.current?.replaceChildren();
    };
  }, [credentials.token]);

  const toggleMic = async () => {
    const next = !mic;
    if (!next) await stopTranscription();
    await roomRef.current?.localParticipant.setMicrophoneEnabled(next);
    if (next) beginTranscription(roomRef.current?.localParticipant.getTrackPublication(Track.Source.Microphone)?.audioTrack?.mediaStreamTrack);
    setMic(next);
  };
  const toggleCamera = async () => {
    const next = !camera;
    await roomRef.current?.localParticipant.setCameraEnabled(next);
    setCamera(next);
  };
  const end = async () => {
    if (ending) return;
    setEnding(true);
    try {
      await stopTranscription();
      await new Promise((resolve) => window.setTimeout(resolve, 600));
      await flush();
      if (onEnd) await onEnd();
      roomRef.current?.disconnect();
    } catch (error) {
      setConnection(error instanceof Error ? error.message : "Could not end the interview. Try again.");
      if (mic) beginTranscription(roomRef.current?.localParticipant.getTrackPublication(Track.Source.Microphone)?.audioTrack?.mediaStreamTrack);
    } finally { setEnding(false); }
  };
  const leave = async () => {
    await stopTranscription();
    await new Promise((resolve) => window.setTimeout(resolve, 600));
    await flush();
    roomRef.current?.disconnect();
    onLeave();
  };

  useImperativeHandle(ref, () => ({ toggleMic, toggleCamera, leave, end }));

  return <div className="interview-call">
    <div className="interview-video remote" ref={remoteRef}><span>{role === "interviewer" ? "Waiting for candidate video" : "Waiting for interviewer video"}</span></div>
    <div className="interview-remote-audio" ref={remoteAudioRef} />
    <div className="interview-video local" ref={localRef}><span>Your camera</span></div>
    {role === "candidate" && <div className="interview-controls">
      <span className="muted" role="status">{connection}</span>
      <button type="button" className="secondary" onClick={() => void toggleMic()}>{mic ? "Mute" : "Unmute"}</button>
      <button type="button" className="secondary" onClick={() => void toggleCamera()}>{camera ? "Camera off" : "Camera on"}</button>
      {onEnd && <button type="button" className="danger-button" disabled={ending} onClick={() => void end()}>{ending ? "Ending…" : "End interview"}</button>}
      <button type="button" className="secondary" onClick={() => void leave()}>{connection.startsWith("Disconnected") ? "Rejoin options" : "Leave call"}</button>
    </div>}
    {transcriptionWarning && <p className="error" role="alert">Transcription is delayed. The call continues and transcription will reconnect while this page stays open. A long outage may leave a gap in the transcript.</p>}
  </div>;
});

export default InterviewCall;
