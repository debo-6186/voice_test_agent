// JS port of vqlab/fx/network.py's frame-level network impairments: burst
// packet loss (Gilbert-Elliott) with PLC concealment, and a fixed-depth
// jitter buffer. Same algorithms and parameter meanings as the Python
// engine; not bit-identical (Box-Muller here vs. numpy's Gaussian), same
// spirit as the rest of this demo - a different implementation sharing only
// the parameter definitions.

function dbToLin(db) {
  return Math.pow(10, db / 20);
}

function frameSamples(x, n) {
  const nFrames = Math.ceil(x.length / n) || 1;
  const frames = [];
  for (let i = 0; i < nFrames; i++) {
    const frame = new Float32Array(n);
    const start = i * n;
    const end = Math.min(x.length, start + n);
    if (end > start) frame.set(x.subarray(start, end));
    frames.push(frame);
  }
  return frames;
}

function flatten(frames, targetLength) {
  const n = frames[0].length;
  const out = new Float32Array(frames.length * n);
  for (let i = 0; i < frames.length; i++) out.set(frames[i], i * n);
  return out.slice(0, targetLength);
}

// One Gaussian sample via Box-Muller, driven by a [0,1) uniform generator.
function gaussianRandom(rng) {
  let u1 = 0;
  while (u1 === 0) u1 = rng();
  const u2 = rng();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

// Two-state burst loss model (ITU-T G.1050 style). `lossPct` is the mean
// loss rate; `burstLen` the mean number of consecutive lost frames.
export function gilbertElliott(nFrames, lossPct, burstLen, rng) {
  const pLoss = Math.max(0, Math.min(lossPct / 100, 0.95));
  const lost = new Array(nFrames).fill(false);
  if (pLoss <= 0) return lost;
  const bl = Math.max(1, burstLen);
  const r = 1 / bl; // bad -> good
  let q = (r * pLoss) / Math.max(1 - pLoss, 1e-12); // good -> bad
  q = Math.min(q, 1);
  let bad = false;
  for (let i = 0; i < nFrames; i++) {
    if (bad) {
      lost[i] = true;
      if (rng() < r) bad = false;
    } else if (rng() < q) {
      bad = true;
      lost[i] = true;
    }
  }
  return lost;
}

// Drop frames per gilbertElliott and conceal them the way a real decoder
// would. plc: 'none' (silence), 'hold' (repeat+attenuate last good frame,
// G.711 Appendix I style), 'noise' (comfort noise at the local floor).
export function packetLoss(samples, sr, { lossPct = 5.0, burstLen = 3.0, frameMs = 20.0, plc = 'hold', seed = 0 } = {}) {
  const n = Math.max(1, Math.round((sr * frameMs) / 1000));
  const frames = frameSamples(samples, n);
  const rng = mulberry32Local(seed);
  const lost = gilbertElliott(frames.length, lossPct, burstLen, rng);

  let lastGood = new Float32Array(n);
  let run = 0;
  const out = frames.map((f) => f.slice());
  for (let i = 0; i < out.length; i++) {
    if (!lost[i]) {
      lastGood = out[i].slice();
      run = 0;
      continue;
    }
    run++;
    if (plc === 'none') {
      out[i] = new Float32Array(n);
    } else if (plc === 'noise') {
      const f = new Float32Array(n);
      for (let j = 0; j < n; j++) f[j] = gaussianRandom(rng) * 0.002;
      out[i] = f;
    } else {
      const g = dbToLin(-6.0 * run);
      const f = new Float32Array(n);
      for (let j = 0; j < n; j++) {
        let v = lastGood[j] * g;
        if (run > 1) v *= 1 - 0.25 * rng();
        f[j] = v;
      }
      out[i] = f;
    }
  }
  return { samples: flatten(out, samples.length), stats: { frames: frames.length, framesLost: lost.filter(Boolean).length } };
}

// Simulate a fixed-size playout buffer: frames arrive with random delay;
// anything later than the buffer depth is discarded (it becomes loss), and
// survivors play in arrival order - jitter sounds like loss plus
// scrambling, not like an echo.
export function jitter(samples, sr, { jitterMs = 40.0, bufferMs = 60.0, frameMs = 20.0, seed = 0 } = {}) {
  const n = Math.max(1, Math.round((sr * frameMs) / 1000));
  const frames = frameSamples(samples, n);
  const k = frames.length;
  const rng = mulberry32Local(seed);

  const delay = new Float64Array(k);
  for (let i = 0; i < k; i++) delay[i] = Math.abs(gaussianRandom(rng) * jitterMs);
  const late = new Array(k);
  const arrival = new Float64Array(k);
  for (let i = 0; i < k; i++) {
    late[i] = delay[i] > bufferMs;
    arrival[i] = i * frameMs + delay[i];
  }
  const order = Array.from({ length: k }, (_, i) => i).sort(
    (a, b) => arrival[a] - arrival[b] || a - b,
  );

  const out = frames.map(() => new Float32Array(n));
  let lastGood = new Float32Array(n);
  let played = 0;
  for (const idx of order) {
    if (late[idx]) continue;
    if (played >= k) break;
    out[played] = frames[idx].slice();
    lastGood = frames[idx];
    played++;
  }
  const atten = dbToLin(-12.0);
  for (let i = played; i < k; i++) {
    const f = new Float32Array(n);
    for (let j = 0; j < n; j++) f[j] = lastGood[j] * atten;
    out[i] = f;
  }
  return { samples: flatten(out, samples.length), stats: { frames: k, framesLateDiscarded: late.filter(Boolean).length } };
}

// Local copy of the mulberry32 seeded PRNG (prng.js's version is imported
// separately for bed-offset selection) to keep this module self-contained.
function mulberry32Local(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function applyNetworkImpairment(samples, sr, network, seed) {
  if (network.type === 'packet_loss') {
    return packetLoss(samples, sr, {
      lossPct: network.loss_pct,
      burstLen: network.burst_len,
      plc: network.plc,
      seed,
    }).samples;
  }
  if (network.type === 'jitter') {
    return jitter(samples, sr, {
      jitterMs: network.jitter_ms,
      bufferMs: network.buffer_ms,
      seed,
    }).samples;
  }
  throw new Error(`unknown network impairment type: ${network.type}`);
}
