"use client";

/**
 * The live interview call: full-screen, brand-calm, honest.
 *
 * The browser authenticates one server-controlled audio relay with a one-use
 * ticket. Provider connections stay on the server. One AudioContext carries
 * everything: the mic is resampled to 16 kHz PCM in a worklet, replies play
 * from a scheduled 24 kHz buffer queue with barge-in flush, and when the
 * study keeps audio, mic + reply are mixed into a MediaRecorder so the
 * session lands with its recording. Both transcription streams are folded
 * into timed turns that finalize into interview segments.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, PhoneOff } from "lucide-react";

import HeroField from "@/components/brand/hero-field";
import PublicLegalFooter from "@/components/public-legal-footer";
import { Button } from "@/components/ui/button";
import { API_URL } from "@/lib/api";
import { formatClock } from "@/lib/format";
import type { LiveVoiceSessionConfig, VoiceTurn } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface LiveSessionResult {
  turns: VoiceTurn[];
  duration_ms: number;
  audio_base64?: string;
  aborted: boolean;
  // Display-only; never used to decide retention, settlement, or provider usage.
  endReason?: RelayEndReason | "user";
}

type Phase = "connecting" | "live" | "reconnecting" | "closing";
type Speaking = "idle" | "user" | "agent";

type RelayEndReason = "budget" | "time" | "connection" | "unavailable";

/** Preserve an interrupted call's outcome after its overlay has unmounted. */
export function liveSessionInterruptionNotice(
  reason: LiveSessionResult["endReason"],
  saved: boolean,
  german: boolean,
): string | null {
  if (reason !== "connection" && reason !== "unavailable") return null;
  const cause = german
    ? reason === "connection"
      ? "Die Verbindung wurde unterbrochen, bevor das Interview beendet war."
      : "Das Sprachinterview wurde unerwartet unterbrochen."
    : reason === "connection"
      ? "The connection ended before the interview finished."
      : "The spoken interview ended unexpectedly.";
  const outcome = german
    ? saved
      ? "Das bisherige Gespräch wurde gespeichert und ist möglicherweise unvollständig."
      : "Es wurde kein Interview-Transkript gespeichert. Bitte versuchen Sie es erneut."
    : saved
      ? "The conversation captured so far was saved; it may be incomplete."
      : "No interview transcript was saved. Please try again.";
  return `${cause} ${outcome}`;
}

interface LiveRelayMessage {
  setupComplete?: object;
  relayState?: { phase?: "reconnecting" | "live" };
  relayEnd?: { reason?: RelayEndReason };
  serverContent?: {
    interrupted?: boolean;
    modelTurn?: {
      parts?: Array<{ inlineData?: { data?: string } }>;
    };
    inputTranscription?: { text?: string };
    outputTranscription?: { text?: string };
  };
}

interface ScheduledReplyAudio {
  node: AudioBufferSourceNode;
  gain: GainNode;
  startAt: number;
  endAt: number;
  fadeOutAt: number;
}

// Buffer once at the beginning of a phrase, not again between every packet.
const REPLY_START_BUFFER_SECONDS = 0.08;
const REPLY_EDGE_FADE_SECONDS = 0.005;

// The worklet emits roughly ten 100 ms PCM frames per second. Keep a
// generous but bounded bridge for a server-side connection handover. The
// browser keeps its one authenticated socket and waits for the relay to be ready.
const MAX_BUFFERED_AUDIO_CHUNKS = 160;
// An unanswered permission prompt or an open socket without setupComplete
// must not trap the participant. A server-side handover also has a bounded wait.
const LIVE_START_TIMEOUT_MS = 30_000;
const LIVE_CONNECT_TIMEOUT_MS = 20_000;

const WORKLET_CODE = `
class PcmTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.carry = new Float32Array(0);
    this.pos = 0;
    this.out = [];
    this.outLen = 0;
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    let sum = 0;
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
    const rms = Math.sqrt(sum / ch.length);
    const merged = new Float32Array(this.carry.length + ch.length);
    merged.set(this.carry, 0); merged.set(ch, this.carry.length);
    const pcm = [];
    while (this.pos + 1 < merged.length) {
      const base = Math.floor(this.pos);
      const frac = this.pos - base;
      const sample = merged[base] * (1 - frac) + merged[base + 1] * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      pcm.push(clamped < 0 ? clamped * 32768 : clamped * 32767);
      this.pos += this.ratio;
    }
    const consumed = Math.floor(this.pos);
    this.carry = merged.slice(consumed);
    this.pos -= consumed;
    if (pcm.length) { this.out.push(Int16Array.from(pcm)); this.outLen += pcm.length; }
    if (this.outLen >= 1600) {
      const packet = new Int16Array(this.outLen); let off = 0;
      for (const part of this.out) { packet.set(part, off); off += part.length; }
      this.out = []; this.outLen = 0;
      this.port.postMessage({ pcm: packet.buffer, rms }, [packet.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm-tap", PcmTap);`;

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode.apply(
      null,
      Array.from(bytes.subarray(i, i + 8192)),
    );
  }
  return btoa(binary);
}

async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  return toBase64(new Uint8Array(buffer));
}

function relaySocketURL(config: LiveVoiceSessionConfig): string | null {
  if (
    config.mode !== "live" || config.transport !== "relay" ||
    typeof config.id !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(config.id) ||
    typeof config.token !== "string" || !config.token ||
    config.token.trim() !== config.token || config.token.length > 8192 ||
    typeof config.ws_url !== "string"
  ) return null;

  try {
    const expected = new URL(API_URL);
    const localDevelopment = expected.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(expected.hostname);
    if (
      (expected.protocol !== "https:" && !localDevelopment) ||
      expected.username || expected.password || expected.search || expected.hash ||
      expected.pathname !== "/"
    ) return null;
    expected.protocol = expected.protocol === "https:" ? "wss:" : "ws:";
    expected.pathname = `/voice/sessions/${encodeURIComponent(config.id)}/relay`;
    const supplied = new URL(config.ws_url, expected);
    // Canonical equality also rejects empty query/fragment suffixes, encoded
    // path aliases and credentials. Never send a ticket to a response-chosen host.
    if (
      supplied.origin !== expected.origin || supplied.pathname !== expected.pathname ||
      supplied.username || supplied.password || supplied.search || supplied.hash ||
      (config.ws_url !== expected.href && config.ws_url !== expected.pathname)
    ) return null;
    return expected.href;
  } catch {
    return null;
  }
}

export default function LiveSession({
  config,
  title,
  onFinished,
  onError,
  showPublicLegalFooter = false,
}: {
  config: LiveVoiceSessionConfig;
  title?: string;
  onFinished: (result: LiveSessionResult) => void;
  onError: (message: string) => void;
  showPublicLegalFooter?: boolean;
}) {
  const german = config.language === "de";
  const [phase, setPhase] = useState<Phase>("connecting");
  const [speaking, setSpeaking] = useState<Speaking>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [userLine, setUserLine] = useState("");
  const [agentLine, setAgentLine] = useState("");
  const [connectionNotice, setConnectionNotice] = useState("");

  const engine = useRef<{
    ws: WebSocket | null;
    ctx: AudioContext | null;
    micStream: MediaStream | null;
    recorder: MediaRecorder | null;
    recorded: Blob[];
    playTime: number;
    sources: ScheduledReplyAudio[];
    startedAt: number;
    turns: VoiceTurn[];
    currentRole: "interviewer" | "participant" | null;
    currentText: string;
    currentStart: number;
    quietAgentTimer: number | null;
    closed: boolean;
    finishing: boolean;
    relayEnded: boolean;
    resolveRelayStop: (() => void) | null;
    ready: boolean;
    startupTimer: number | null;
    connectionTimer: number | null;
    audioSendTimer: number | null;
    nextAudioSendAt: number;
    pendingAudio: Array<{ data: string; durationMs: number }>;
  }>({
    ws: null,
    ctx: null,
    micStream: null,
    recorder: null,
    recorded: [],
    playTime: 0,
    sources: [],
    startedAt: 0,
    turns: [],
    currentRole: null,
    currentText: "",
    currentStart: 0,
    quietAgentTimer: null,
    closed: false,
    finishing: false,
    relayEnded: false,
    resolveRelayStop: null,
    ready: false,
    startupTimer: null,
    connectionTimer: null,
    audioSendTimer: null,
    nextAudioSendAt: 0,
    pendingAudio: [],
  });

  const now = () =>
    engine.current.startedAt ? performance.now() - engine.current.startedAt : 0;

  const closeTurn = useCallback(() => {
    const state = engine.current;
    if (state.currentRole && state.currentText.trim()) {
      state.turns.push({
        role: state.currentRole,
        text: state.currentText.trim(),
        start_ms: Math.max(0, Math.round(state.currentStart)),
        end_ms: Math.max(0, Math.round(now())),
      });
    }
    state.currentRole = null;
    state.currentText = "";
  }, []);

  const appendTranscript = useCallback(
    (role: "interviewer" | "participant", text: string) => {
      const state = engine.current;
      if (state.currentRole !== role) {
        closeTurn();
        state.currentRole = role;
        state.currentStart = now();
      }
      state.currentText += text;
      if (role === "participant") setUserLine(state.currentText);
      else setAgentLine(state.currentText);
    },
    [closeTurn],
  );

  const flushPlayback = useCallback(() => {
    const state = engine.current;
    const sources = state.sources;
    state.sources = [];
    const at = state.ctx?.currentTime ?? 0;
    for (const { node, gain, startAt } of sources) {
      try {
        if (startAt > at) {
          node.stop(at);
        } else {
          // A hard stop at a non-zero PCM sample creates an audible click.
          if (typeof gain.gain.cancelAndHoldAtTime === "function") {
            gain.gain.cancelAndHoldAtTime(at);
          } else {
            const currentGain = gain.gain.value;
            gain.gain.cancelScheduledValues(at);
            gain.gain.setValueAtTime(currentGain, at);
          }
          gain.gain.linearRampToValueAtTime(0, at + REPLY_EDGE_FADE_SECONDS);
          node.stop(at + REPLY_EDGE_FADE_SECONDS);
        }
      } catch {
        // An unsupported automation method must never defeat barge-in.
        try { node.stop(at); } catch { /* node may already be done */ }
      }
    }
    state.playTime = 0;
    setSpeaking("idle");
  }, []);

  const playChunk = useCallback((base64: string, destination: AudioNode[]) => {
    const state = engine.current;
    const ctx = state.ctx;
    if (!ctx) return;
    const raw = atob(base64);
    if (!raw.length || raw.length % 2 !== 0) return;
    const pcm = new Int16Array(raw.length / 2);
    for (let i = 0; i < pcm.length; i++) {
      pcm[i] = ((raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8)) << 16) >> 16;
    }
    // 24 kHz buffers resample to the context rate on playback
    const buffer = ctx.createBuffer(1, pcm.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    const gain = ctx.createGain();
    node.connect(gain);
    for (const target of destination) gain.connect(target);
    const previous = state.sources.at(-1);
    // Preserve sample-contiguous playback even when the next packet arrives
    // less than 40 ms before the current one ends. Rebuffer after an underrun
    // or once the existing tail is fading. Never undo an audible fade.
    const contiguous = previous && previous.fadeOutAt > ctx.currentTime + 0.002;
    const at = contiguous ? previous.endAt : ctx.currentTime + REPLY_START_BUFFER_SECONDS;
    if (contiguous) previous.gain.gain.cancelScheduledValues(previous.fadeOutAt);
    const fade = Math.min(REPLY_EDGE_FADE_SECONDS, buffer.duration / 3);
    const endAt = at + buffer.duration;
    const fadeOutAt = endAt - fade;
    gain.gain.setValueAtTime(contiguous ? 1 : 0, at);
    if (!contiguous) gain.gain.linearRampToValueAtTime(1, at + fade);
    // A final packet (or network underrun) ends gently. A timely next packet
    // cancels this tail fade, so continuous speech has no per-packet dips.
    gain.gain.setValueAtTime(1, fadeOutAt);
    gain.gain.linearRampToValueAtTime(0, endAt);
    node.start(at);
    state.playTime = endAt;
    const scheduled = { node, gain, startAt: at, endAt, fadeOutAt };
    state.sources.push(scheduled);
    node.onended = () => {
      node.disconnect();
      gain.disconnect();
      state.sources = state.sources.filter((source) => source !== scheduled);
      if (state.sources.length === 0) setSpeaking("idle");
    };
  }, []);

  const teardown = useCallback(() => {
    const state = engine.current;
    state.closed = true;
    state.resolveRelayStop?.();
    if (state.quietAgentTimer) window.clearTimeout(state.quietAgentTimer);
    if (state.startupTimer !== null) window.clearTimeout(state.startupTimer);
    if (state.connectionTimer !== null) window.clearTimeout(state.connectionTimer);
    if (state.audioSendTimer !== null) window.clearTimeout(state.audioSendTimer);
    state.startupTimer = null;
    state.connectionTimer = null;
    state.audioSendTimer = null;
    state.pendingAudio = [];
    if (state.ws) {
      try {
        state.ws.close();
      } catch {
        // already closed
      }
      state.ws = null;
    }
    if (state.micStream) {
      for (const track of state.micStream.getTracks()) track.stop();
      state.micStream = null;
    }
    if (state.recorder && state.recorder.state !== "inactive") {
      try {
        state.recorder.stop();
      } catch {
        // the recorder may already be stopping after a concurrent close
      }
    }
    state.recorder = null;
    if (state.ctx) {
      void state.ctx.close();
      state.ctx = null;
    }
  }, []);

  const reportError = useCallback(
    (message: string) => {
      if (engine.current.closed) return;
      teardown();
      onError(message);
    },
    [onError, teardown],
  );

  const finish = useCallback(
    async (aborted: boolean, endReason: LiveSessionResult["endReason"] = "user") => {
      const state = engine.current;
      if (state.closed || state.finishing || phase === "closing") return;
      state.finishing = true;
      setPhase("closing");
      state.ready = false;
      state.pendingAudio = [];
      if (state.startupTimer !== null) window.clearTimeout(state.startupTimer);
      if (state.connectionTimer !== null) window.clearTimeout(state.connectionTimer);
      if (state.audioSendTimer !== null) window.clearTimeout(state.audioSendTimer);
      state.startupTimer = null;
      state.connectionTimer = null;
      state.audioSendTimer = null;
      if (state.micStream) {
        for (const track of state.micStream.getTracks()) track.stop();
        state.micStream = null;
      }
      const socket = state.ws;
      if (!state.relayEnded && socket?.readyState === WebSocket.OPEN) {
        // Give the relay a bounded chance to durably close its provider session
        // before the existing caller submits the transcript for finalization.
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(done, 3_000);
          function done() {
            window.clearTimeout(timer);
            state.resolveRelayStop = null;
            resolve();
          }
          state.resolveRelayStop = done;
          try {
            socket.send(JSON.stringify({ type: "stop" }));
          } catch {
            done();
          }
        });
      }
      if (state.closed) return;
      closeTurn();
      const duration = Math.round(now());
      const recorder = state.recorder;
      const recordingDone = new Promise<void>((resolve) => {
        if (!recorder || recorder.state === "inactive") return resolve();
        recorder.onstop = () => resolve();
        recorder.stop();
      });
      teardown();
      await recordingDone;
      let audioBase64: string | undefined;
      if (!aborted && state.recorded.length > 0 && config.retention === "keep") {
        const blob = new Blob(state.recorded, { type: "audio/webm" });
        if (blob.size > 0 && blob.size < 95_000_000) {
          audioBase64 = await blobToBase64(blob);
        }
      }
      onFinished({
        // The relay has already persisted the authenticated provider turns.
        // Local captions are display-only and must not hit request length
        // limits or replace that durable transcript during finalization.
        turns: [],
        duration_ms: duration,
        audio_base64: audioBase64,
        // Missing local captions do not imply an empty server transcript.
        aborted,
        endReason,
      });
    },
    [closeTurn, config.retention, onFinished, phase, teardown],
  );

  useEffect(() => {
    const state = engine.current;
    let cancelled = false;
    const relayURL = relaySocketURL(config);
    const unavailableMessage = german
      ? "Das Sprachinterview konnte gerade nicht verbunden werden. Bitte versuche es erneut."
      : "The spoken interview could not connect right now. Please try again.";
    if (!relayURL) {
      reportError(unavailableMessage);
      return teardown;
    }
    const connectionTimeoutMessage = german
      ? "Die Verbindung dauert zu lange. Bitte prüfe deine Internetverbindung und versuche es erneut."
      : "Connecting is taking too long. Please check your internet connection and try again.";

    function endRelaySession(reason: RelayEndReason) {
      if (state.closed) return;
      state.relayEnded = true;
      state.resolveRelayStop?.();
      if (state.finishing) return;
      if (!state.startedAt) {
        reportError(reason === "budget"
          ? german
            ? "Für dieses Interview ist aktuell keine Kapazität verfügbar. Bitte versuche es später erneut."
            : "There is currently no capacity available for this interview. Please try again later."
          : unavailableMessage);
        return;
      }
      const notices: Record<RelayEndReason, string> = german ? {
        budget: "Die Kapazitätsgrenze für dieses Interview ist erreicht. Dein Gespräch wird gespeichert.",
        time: "Die vereinbarte Interviewdauer ist erreicht. Dein Gespräch wird gespeichert.",
        connection: "Die Verbindung wurde unterbrochen. Dein bisheriges Gespräch wird gespeichert.",
        unavailable: "Das Sprachinterview ist gerade nicht verfügbar. Dein bisheriges Gespräch wird gespeichert.",
      } : {
        budget: "This interview has reached its capacity limit. Your conversation is being saved.",
        time: "The agreed interview duration has been reached. Your conversation is being saved.",
        connection: "The connection was interrupted. Your conversation so far is being saved.",
        unavailable: "The spoken interview is unavailable right now. Your conversation so far is being saved.",
      };
      setConnectionNotice(notices[reason] ?? notices.connection);
      void finish(false, reason);
    }
    state.startupTimer = window.setTimeout(() => {
      state.startupTimer = null;
      reportError(
        german
          ? "Der Start dauert zu lange. Bitte prüfe die Mikrofonfreigabe und deine Internetverbindung und versuche es erneut."
          : "Starting is taking too long. Please check microphone permission and your internet connection, then try again.",
      );
    }, LIVE_START_TIMEOUT_MS);

    async function start() {
      let micStream: MediaStream;
      try {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch {
        reportError(
          german
            ? "Kein Mikrofonzugriff. Bitte erlauben und erneut starten."
            : "No microphone access. Allow it and start again.",
        );
        return;
      }
      if (cancelled || state.closed) {
        for (const track of micStream.getTracks()) track.stop();
        return;
      }
      state.micStream = micStream;
      const ctx = new AudioContext();
      state.ctx = ctx;
      const url = URL.createObjectURL(
        new Blob([WORKLET_CODE], { type: "application/javascript" }),
      );
      try {
        await ctx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      if (cancelled || state.closed) return;
      const micSource = ctx.createMediaStreamSource(micStream);
      const tap = new AudioWorkletNode(ctx, "pcm-tap");
      micSource.connect(tap);

      // recording sink: microphone + agent replies mixed into one stream
      const playbackTargets: AudioNode[] = [ctx.destination];
      if (config.retention === "keep" && typeof MediaRecorder !== "undefined") {
        const sink = ctx.createMediaStreamDestination();
        micSource.connect(sink);
        playbackTargets.push(sink);
        try {
          const recorder = new MediaRecorder(sink.stream, {
            mimeType: "audio/webm;codecs=opus",
          });
          recorder.ondataavailable = (event) => {
            if (event.data.size > 0) state.recorded.push(event.data);
          };
          recorder.start(2_000);
          state.recorder = recorder;
        } catch {
          // recording is best effort; the transcript is the record
        }
      }

      function drainAudio() {
        const socket = state.ws;
        if (
          state.closed || state.finishing || !state.ready ||
          !socket || socket.readyState !== WebSocket.OPEN ||
          state.audioSendTimer !== null || state.pendingAudio.length === 0
        ) return;
        const wait = state.nextAudioSendAt - performance.now();
        if (wait > 0) {
          state.audioSendTimer = window.setTimeout(() => {
            state.audioSendTimer = null;
            drainAudio();
          }, Math.ceil(wait));
          return;
        }
        const frame = state.pendingAudio.shift()!;
        try {
          socket.send(JSON.stringify({
            realtimeInput: { audio: { data: frame.data, mimeType: "audio/pcm;rate=16000" } },
          }));
          // Pacing follows PCM duration, not callback timing. In particular,
          // a resumed connection cannot burst its buffered speech past the
          // server's real-time audio gate or reorder it with fresh mic input.
          state.nextAudioSendAt = performance.now() + frame.durationMs;
          drainAudio();
        } catch {
          endRelaySession("connection");
        }
      }

      // The tap stays active during server-side handovers. Both buffered and
      // fresh frames pass through one bounded, real-time-paced FIFO.
      tap.port.onmessage = (frame) => {
        if (state.closed || state.finishing) return;
        const { pcm, rms } = frame.data as { pcm: ArrayBuffer; rms: number };
        setLevel(rms);
        if (rms > 0.02) setSpeaking((current) => (current === "agent" ? current : "user"));
        if (!state.startedAt) return;
        if (state.pendingAudio.length >= MAX_BUFFERED_AUDIO_CHUNKS) {
          state.pendingAudio.shift();
        }
        state.pendingAudio.push({
          data: toBase64(new Uint8Array(pcm)),
          durationMs: pcm.byteLength / 32,
        });
        drainAudio();
      };

      function connect(socketURL: string) {
        if (state.closed) return;
        const ws = new WebSocket(socketURL);
        state.ws = ws;
        state.ready = false;
        let authenticated = false;
        let messageQueue = Promise.resolve();

        function waitForRelay() {
          if (state.connectionTimer !== null) return;
          state.connectionTimer = window.setTimeout(() => {
            if (state.closed || ws !== state.ws || state.ready || state.finishing) return;
            state.connectionTimer = null;
            if (state.startedAt) endRelaySession("connection");
            else reportError(connectionTimeoutMessage);
          }, LIVE_CONNECT_TIMEOUT_MS);
        }

        function markLive() {
          if (state.closed || state.finishing || ws.readyState !== WebSocket.OPEN) return;
          if (state.startupTimer !== null) window.clearTimeout(state.startupTimer);
          if (state.connectionTimer !== null) window.clearTimeout(state.connectionTimer);
          state.startupTimer = null;
          state.connectionTimer = null;
          if (!state.startedAt) state.startedAt = performance.now();
          state.ready = true;
          drainAudio();
          setPhase("live");
        }

        waitForRelay();
        ws.onopen = () => {
          if (state.closed || state.finishing || ws !== state.ws || authenticated) return;
          authenticated = true;
          try {
            ws.send(JSON.stringify({ authenticate: { token: config.token } }));
          } catch {
            endRelaySession("connection");
          }
        };
        async function handleMessage(event: MessageEvent) {
          const text = typeof event.data === "string" ? event.data : await event.data.text();
          if (state.closed || ws !== state.ws || !authenticated) return;
          let msg: LiveRelayMessage;
          try {
            msg = JSON.parse(text) as LiveRelayMessage;
          } catch {
            return;
          }
          if (!msg || typeof msg !== "object" || Array.isArray(msg)) return;
          if (msg.relayEnd) {
            const reason = msg.relayEnd.reason;
            endRelaySession(
              reason === "budget" || reason === "time" || reason === "unavailable"
                ? reason : "connection",
            );
            return;
          }
          if (msg.relayState?.phase === "reconnecting" && state.startedAt && !state.finishing) {
            state.ready = false;
            if (state.audioSendTimer !== null) window.clearTimeout(state.audioSendTimer);
            state.audioSendTimer = null;
            setPhase("reconnecting");
            flushPlayback();
            waitForRelay();
            return;
          }
          if (msg.setupComplete || (msg.relayState?.phase === "live" && state.startedAt)) {
            markLive();
            return;
          }
          if (!state.startedAt) return;
          const content = msg.serverContent || {};
          if (content.interrupted) flushPlayback();
          for (const part of (content.modelTurn || {}).parts || []) {
            const inline = part.inlineData || {};
            if (inline.data) {
              setSpeaking("agent");
              playChunk(inline.data, playbackTargets);
            }
          }
          if (content.inputTranscription?.text) {
            appendTranscript("participant", content.inputTranscription.text);
          }
          if (content.outputTranscription?.text) {
            appendTranscript("interviewer", content.outputTranscription.text);
          }
        }

        ws.onmessage = (event) => {
          // Blob decoding must not let an end frame overtake the final
          // transcript or audio frame. Process messages in wire order.
          messageQueue = messageQueue.then(() => handleMessage(event)).catch(() => {
            endRelaySession("connection");
          });
          return messageQueue;
        };
        ws.onclose = () => {
          if (state.closed || ws !== state.ws) return;
          if (state.connectionTimer !== null) window.clearTimeout(state.connectionTimer);
          state.connectionTimer = null;
          state.ready = false;
          // Relay tickets are one-use. Never reconnect the browser socket or
          // fall back to a direct provider connection; preserve partial work.
          void messageQueue.then(() => endRelaySession("connection"));
        };
        ws.onerror = () => {
          if (state.closed || ws !== state.ws) return;
          void messageQueue.then(() => endRelaySession("connection"));
        };
      }

      connect(relayURL!);
    }

    void start().catch(() => {
      reportError(
        german
          ? "Das Interview konnte auf diesem Gerät nicht gestartet werden."
          : "The interview could not start on this device.",
      );
    });
    return () => {
      cancelled = true;
      teardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (engine.current.startedAt) setElapsed(now());
    }, 500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const nearLimit =
    elapsed > (config.max_session_minutes - 2) * 60_000 && phase === "live";
  useEffect(() => {
    if (
      elapsed >= config.max_session_minutes * 60_000 &&
      (phase === "live" || phase === "reconnecting")
    ) {
      void finish(false, "time");
    }
  }, [config.max_session_minutes, elapsed, finish, phase]);

  const orbScale = 1 + Math.min(0.35, level * 4);

  return (
    <div className="fixed inset-0 z-50 bg-ivory p-2.5 md:p-3">
      <div className="relative flex h-full flex-col overflow-hidden rounded-[22px] bg-pine md:rounded-[26px]">
        <HeroField className="absolute inset-0" />
        {/* legibility for the caption zone without boxing anything in */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-72 bg-gradient-to-t from-pine/80 via-pine/30 to-transparent"
        />
        <div className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-between px-6 py-6 md:px-10">
          <div className="flex w-full max-w-5xl items-center justify-between">
            <div>
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.24em] text-ivory/60">
                {german ? "KI-Interview · offengelegt" : "AI interview · disclosed"}
              </p>
              <p className="mt-1 text-[0.875rem] font-medium text-ivory/95">
                {title ?? (german ? "Pilot-Gespräch" : "Pilot conversation")}
              </p>
            </div>
            <div className="text-right">
              <p className="font-mono text-[1.125rem] text-ivory">
                {formatClock(elapsed)}
              </p>
              <p
                className={cn(
                  "font-mono text-[0.625rem] uppercase tracking-[0.18em]",
                  nearLimit ? "text-amber-300" : "text-ivory/50",
                )}
              >
                {german ? "max." : "max"} {config.max_session_minutes} min
              </p>
            </div>
          </div>

          <div className="flex flex-col items-center gap-8">
            <div className="relative grid size-44 place-items-center">
              <span
                className={cn(
                  "absolute inset-0 rounded-full transition-transform duration-150",
                  speaking === "agent" ? "bg-ivory/15" : "bg-pine/30",
                )}
                style={{ transform: `scale(${speaking === "agent" ? 1.18 : orbScale})` }}
              />
              <span
                className={cn(
                  "absolute inset-4 rounded-full transition-colors",
                  speaking === "agent"
                    ? "animate-pulse bg-ivory/20"
                    : speaking === "user"
                      ? "bg-pine/50"
                      : "bg-pine/35",
                )}
                style={{
                  transform: speaking === "user" ? `scale(${orbScale})` : undefined,
                }}
              />
              <span className="relative grid size-24 place-items-center rounded-full bg-ivory text-pine shadow-lg">
                {phase === "connecting" ? (
                  <Loader2 className="size-7 animate-spin" />
                ) : (
                  <Mic className="size-7" />
                )}
              </span>
            </div>
            <p className="text-[0.9375rem] font-medium text-ivory/95 drop-shadow-sm">
              {phase === "connecting"
                ? german
                  ? "Verbinde…"
                  : "Connecting…"
                : phase === "reconnecting"
                  ? german
                    ? "Kurze Störung, verbinde neu…"
                    : "Brief hiccup, reconnecting…"
                : phase === "closing"
                  ? german
                    ? "Speichere das Gespräch…"
                    : "Saving the conversation…"
                  : speaking === "agent"
                    ? german
                      ? "Sie spricht…"
                      : "She is speaking…"
                    : german
                      ? "Sie hört zu. Sprich einfach."
                      : "She is listening. Just talk."}
            </p>
          </div>

          <div className="flex w-full max-w-3xl flex-col items-center gap-3 text-center">
            <p className="min-h-5 w-full truncate text-[0.75rem] text-ivory/60">
              {userLine ? `„${userLine.slice(-140)}“` : ""}
            </p>
            <div className="min-h-[3.25rem]">
              <p className="font-mono text-[0.5625rem] uppercase tracking-[0.22em] text-ivory/55">
                {german ? "KI-Interviewerin" : "AI interviewer"}
              </p>
              <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-ivory/95 drop-shadow-sm">
                {agentLine
                  ? agentLine.length > 220
                    ? `…${agentLine.slice(-220)}`
                    : agentLine
                  : "…"}
              </p>
            </div>
            <Button
              onClick={() => void finish(!engine.current.startedAt)}
              disabled={phase === "closing"}
              className="mt-1 h-11 rounded-full bg-ivory px-7 text-pine shadow-lg hover:bg-ivory/90"
            >
              {phase === "closing" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <PhoneOff className="size-4" />
              )}
              {phase === "connecting"
                ? german
                  ? "Verbindung abbrechen"
                  : "Cancel connection"
                : german
                  ? "Gespräch beenden"
                  : "End conversation"}
            </Button>
            {connectionNotice && (
              <p role="status" className="max-w-lg text-[0.8125rem] leading-relaxed text-ivory/80">
                {connectionNotice}
              </p>
            )}
            {showPublicLegalFooter && (
              <PublicLegalFooter
                inverse
                className="pt-1 text-[0.5rem] tracking-[0.1em]"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
