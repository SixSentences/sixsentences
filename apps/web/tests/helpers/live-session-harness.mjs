import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(
  new URL("../../src/components/voice/live-session.tsx", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;

export function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

// Exercise the real component's effects without a network, microphone, React
// renderer, or real-time sleeps. Explicit rerenders expose current controls.
export function createLiveSessionHarness({
  microphone,
  worklet,
  maxSessionMinutes = 30,
  apiUrl = "https://api.example.test",
  config = {},
} = {}) {
  let clock = 1_000;
  let nextTimer = 1;
  const timers = new Map();
  const slots = [];
  const effects = [];
  const cleanups = [];
  let cursor = 0;
  let mounted = false;
  const errors = [];
  const results = [];
  const sockets = [];
  const contexts = [];
  const taps = [];
  const playback = [];
  const recorders = [];
  const microphoneRequests = [];
  const track = { stopped: false, stop() { this.stopped = true; } };
  const stream = { getTracks: () => [track] };
  const schedule = (callback, delay, repeat = false) => {
    const id = nextTimer++;
    timers.set(id, { callback, due: clock + delay, delay, repeat });
    return id;
  };
  const flush = async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  };

  class Socket {
    static OPEN = 1;
    static CLOSING = 2;
    readyState = 0;
    sent = [];
    sentAt = [];
    constructor(url) { this.url = url; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); this.sentAt.push(clock); }
    open() { this.readyState = 1; this.onopen?.(); }
    message(data) { return this.onmessage?.({ data: JSON.stringify(data) }); }
    error(message = "Untrusted provider diagnostic") { this.onerror?.({ message }); }
    close(code = 1000, reason = "") {
      this.readyState = 3;
      this.onclose?.({ code, reason });
    }
  }

  class AudioContext {
    closed = false;
    destination = {};
    audioWorklet = { addModule: () => worklet ?? Promise.resolve() };
    constructor() { contexts.push(this); }
    get currentTime() { return clock / 1000; }
    createMediaStreamSource() { return { connect() {} }; }
    createMediaStreamDestination() { return { stream }; }
    createBuffer(channels, length, sampleRate) {
      const data = new Float32Array(length);
      return { duration: length / sampleRate, getChannelData: () => data };
    }
    createBufferSource() {
      const node = {
        stopped: false, connections: [],
        connect(target) { this.connections.push(target); },
        disconnect() { this.disconnected = true; },
        start(at) { this.startedAt = at; },
        stop(at) { this.stopped = true; this.stoppedAt = at; this.onended?.(); },
      };
      playback.push(node);
      return node;
    }
    createGain() {
      const events = [];
      return {
        connections: [],
        connect(target) { this.connections.push(target); },
        disconnect() { this.disconnected = true; },
        gain: {
          events,
          value: 1,
          setValueAtTime(value, at) { events.push({ kind: "set", value, at }); },
          linearRampToValueAtTime(value, at) { events.push({ kind: "ramp", value, at }); },
          cancelScheduledValues(at) {
            const kept = events.filter((event) => event.at < at);
            events.splice(0, events.length, ...kept);
          },
          cancelAndHoldAtTime(at) { events.push({ kind: "hold", at }); },
        },
      };
    }
    close() { this.closed = true; return Promise.resolve(); }
  }

  class Recorder {
    state = "inactive";
    constructor() { recorders.push(this); }
    start() { this.state = "recording"; }
    chunk(text) { this.ondataavailable?.({ data: new Blob([text], { type: "audio/webm" }) }); }
    stop() { this.state = "inactive"; this.onstop?.(); }
  }

  class BrowserURL extends URL {
    static createObjectURL() { return "blob:test"; }
    static revokeObjectURL() {}
  }

  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => {
        slots[index] = typeof value === "function" ? value(slots[index]) : value;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback: (callback) => callback,
    useEffect(effect) { if (!mounted) effects.push(effect); },
  };
  const jsx = (type, props) => ({ type, props });
  const module = { exports: {} };
  const imports = {
    react,
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "lucide-react": { Loader2: "Loader2", Mic: "Mic", PhoneOff: "PhoneOff" },
    "@/components/brand/hero-field": { default: "HeroField" },
    "@/components/public-legal-footer": { default: "PublicLegalFooter" },
    "@/components/ui/button": { Button: "Button" },
    "@/lib/api": { API_URL: apiUrl },
    "@/lib/format": { formatClock: () => "0:00" },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
  };
  vm.runInNewContext(compiled, {
    exports: module.exports,
    module,
    require(name) {
      if (!(name in imports)) throw new Error(`Unexpected component import: ${name}`);
      return imports[name];
    },
    window: {
      setTimeout: (callback, delay) => schedule(callback, delay),
      clearTimeout: (id) => timers.delete(id),
      setInterval: (callback, delay) => schedule(callback, delay, true),
      clearInterval: (id) => timers.delete(id),
    },
    navigator: { mediaDevices: { getUserMedia: (constraints) => {
      microphoneRequests.push(constraints);
      return microphone ?? Promise.resolve(stream);
    } } },
    performance: { now: () => clock },
    WebSocket: Socket,
    AudioContext,
    AudioWorkletNode: class {
      port = {};
      constructor() { taps.push(this); }
    },
    MediaRecorder: Recorder,
    Blob,
    URL: BrowserURL,
    atob,
    btoa,
    console: { warn() {} },
  });
  const props = {
    config: {
      id: "session-test", mode: "live", transport: "relay", token: "test-only", language: "en",
      ws_url: "wss://api.example.test/voice/sessions/session-test/relay", retention: "transcript_only",
      max_session_minutes: maxSessionMinutes,
      ...config,
    },
    onError: (message) => errors.push(message),
    onFinished: (result) => results.push(result),
  };
  const render = () => {
    cursor = 0;
    return module.exports.default(props);
  };
  render();
  mounted = true;
  for (const effect of effects) {
    const cleanup = effect();
    if (cleanup) cleanups.push(cleanup);
  }

  function findButton(node) {
    if (!node || typeof node !== "object") return null;
    if (node.type === "Button") return node;
    const children = node.props?.children;
    for (const child of Array.isArray(children) ? children.flat(Infinity) : [children]) {
      const found = findButton(child);
      if (found) return found;
    }
    return null;
  }

  return {
    errors, results, sockets, contexts, taps, playback, recorders,
    microphoneRequests, track, stream, flush, render,
    audioFrame(samples = [100, -100], rms = 0.1) {
      const pcm = Int16Array.from(samples).buffer;
      taps[0]?.port.onmessage?.({ data: { pcm, rms } });
      return Buffer.from(pcm).toString("base64");
    },
    button: () => findButton(render()),
    pendingTimeouts: () => [...timers.values()].filter((timer) => !timer.repeat).length,
    unmount() { for (const cleanup of cleanups) cleanup(); },
    async advance(milliseconds) {
      const target = clock + milliseconds;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.due <= target)
          .sort((left, right) => left[1].due - right[1].due)[0];
        if (!next) break;
        const [id, timer] = next;
        clock = timer.due;
        if (timer.repeat) timer.due += timer.delay;
        else timers.delete(id);
        timer.callback();
        await flush();
      }
      clock = target;
      await flush();
    },
  };
}
