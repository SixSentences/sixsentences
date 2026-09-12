import assert from "node:assert/strict";
import test from "node:test";
import { createLiveSessionHarness } from "./helpers/live-session-harness.mjs";

// Deterministic scheduling checks, not a listening/physical-device test.
const pcm = Buffer.alloc(4_800, 0x20).toString("base64"); // 100 ms, PCM16 / 24 kHz.
const audioMessage = (data = pcm) => ({ serverContent: { modelTurn: {
  parts: [{ inlineData: { data, mimeType: "audio/pcm;rate=24000" } }],
} } });

async function connected(config = {}) {
  const live = createLiveSessionHarness({ config });
  await live.flush();
  live.sockets[0].open();
  await live.sockets[0].message({ setupComplete: {} });
  return live;
}

test("a timely packet joins the queue without another 40 ms startup gap", async () => {
  const live = await connected();
  await live.sockets[0].message(audioMessage());
  const first = live.playback[0];
  await live.advance(160); // Initial 80 ms buffer + 100 ms audio leaves 20 ms.
  await live.sockets[0].message(audioMessage());
  const second = live.playback[1];
  assert.equal(second.startedAt, first.startedAt + first.buffer.duration);
  const events = first.connections[0].gain.events;
  assert.equal(events.some((event) => event.kind === "ramp" && event.value === 0), false,
    "the previous future tail fade is removed rather than dipping between chunks");
  assert.equal(second.connections[0].gain.events[0].value, 1);
  live.unmount();
});

test("an actual underrun is rebuffered with gentle edges, without truncating PCM", async () => {
  const live = await connected();
  await live.sockets[0].message(audioMessage());
  const first = live.playback[0];
  await live.advance(200);
  await live.sockets[0].message(audioMessage());
  const second = live.playback[1];
  assert.equal(second.startedAt, live.contexts[0].currentTime + 0.08);
  assert.equal(second.buffer.duration, 0.1);
  assert.equal(second.buffer.getChannelData(0)[0], 0x2020 / 32768);
  for (const node of [first, second]) {
    const events = node.connections[0].gain.events;
    assert.equal(events[0].value, 0);
    assert.equal(events.at(-1).value, 0);
    assert.equal(events.at(-1).at, node.startedAt + node.buffer.duration);
  }
  live.unmount();
});

test("barge-in fades active playback and cancels queued audio for speakers and recording", async () => {
  const live = await connected({ retention: "keep" });
  await live.sockets[0].message(audioMessage());
  await live.sockets[0].message(audioMessage());
  await live.advance(100);
  await live.sockets[0].message({ serverContent: { interrupted: true } });
  const [active, queued] = live.playback;
  assert.equal(active.stoppedAt, live.contexts[0].currentTime + 0.005);
  assert.equal(queued.stoppedAt, live.contexts[0].currentTime);
  assert.equal(active.connections[0].connections.length, 2);
  assert.equal(active.connections[0].gain.events.at(-1).value, 0);
  live.unmount();
});

test("transcript punctuation never generates audio; malformed PCM is not played", async () => {
  const live = await connected();
  await live.sockets[0].message({ serverContent: { outputTranscription: { text: "Why? Yes!" } } });
  await live.sockets[0].message(audioMessage("YQ=="));
  assert.equal(live.playback.length, 0);
  assert.equal(live.errors.length, 0);
  live.unmount();
});

test("older audio engines and automation errors still stop interrupted speech", async () => {
  for (const broken of [false, true]) {
    const live = await connected();
    await live.sockets[0].message(audioMessage());
    await live.advance(100);
    const node = live.playback[0];
    node.connections[0].gain.cancelAndHoldAtTime = broken
      ? () => { throw new Error("Unsupported automation"); }
      : undefined;
    await live.sockets[0].message({ serverContent: { interrupted: true } });
    assert.equal(node.stopped, true);
    assert.equal(node.stoppedAt, live.contexts[0].currentTime + (broken ? 0 : 0.005));
    live.unmount();
  }
});
