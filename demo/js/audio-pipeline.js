// Per-rung degradation pipeline: resample -> level-normalise -> pad -> mix
// cafe noise at target SNR -> resample to codec rate -> G.711 mu-law round
// trip. Mirrors vqlab/corpus.py's ordering and vqlab/audio.py's formulas so
// the browser and the Python engine agree on what each rung means - see
// demo/js/level.js and demo/js/mulaw.js.
//
// Note on mu-law: a WaveShaperNode's curve lookup is linearly interpolated
// between points even with oversample:"none", so it cannot reproduce true
// 8-bit quantization on its own. The actual lossy step is done as an
// explicit array round-trip (mulaw.js) rather than a WaveShaperNode, which
// is the technically-correct way to get real G.711 artifacts rather than a
// smoothed approximation of the companding curve.

import { rmsDbfs, dbToLin, setActiveLevel } from './level.js';
import { mulberry32, sliceBed } from './prng.js';
import { muLawRoundTripArray } from './mulaw.js';
import { applyNetworkImpairment } from './network.js';

export function toMonoFloat32(audioBuffer) {
  const ch = audioBuffer.numberOfChannels;
  if (ch === 1) return audioBuffer.getChannelData(0).slice();
  const out = new Float32Array(audioBuffer.length);
  for (let c = 0; c < ch; c++) {
    const data = audioBuffer.getChannelData(c);
    for (let i = 0; i < out.length; i++) out[i] += data[i] / ch;
  }
  return out;
}

export async function resampleFloat32(samples, srcSr, dstSr) {
  if (srcSr === dstSr) return samples.slice();
  const dstLen = Math.max(1, Math.round((samples.length * dstSr) / srcSr));
  const srcCtx = new OfflineAudioContext(1, samples.length, srcSr);
  const srcBuffer = srcCtx.createBuffer(1, samples.length, srcSr);
  srcBuffer.copyToChannel(samples, 0);

  const ctx = new OfflineAudioContext(1, dstLen, dstSr);
  const src = ctx.createBufferSource();
  src.buffer = srcBuffer;
  src.connect(ctx.destination);
  src.start(0);
  const rendered = await ctx.startRendering();
  return rendered.getChannelData(0).slice();
}

function padSilence(samples, sr, headS, tailS) {
  const headN = Math.round(headS * sr);
  const tailN = Math.round(tailS * sr);
  const out = new Float32Array(headN + samples.length + tailN);
  out.set(samples, headN);
  return out;
}

// Render one rung: mix cafe noise at its target SNR (if any), apply a
// network impairment (if any), then resample to codec rate and G.711
// mu-law round-trip. `seed` selects the bed slice offset and, for network
// rungs, the loss/jitter randomness.
async function renderRung(padded, speechOnlyDb, rung, bedAtWorkSr, workSr, codecSr, seed) {
  let mixed = padded;
  if (rung.snr_db !== null && rung.snr_db !== undefined) {
    const rng = mulberry32(seed);
    const bedSlice = sliceBed(bedAtWorkSr, padded.length, rng);
    const nDb = rmsDbfs(bedSlice);
    const gain = dbToLin(speechOnlyDb - rung.snr_db - nDb);
    mixed = new Float32Array(padded.length);
    for (let s = 0; s < padded.length; s++) {
      mixed[s] = padded[s] + bedSlice[s] * gain;
    }
  }

  if (rung.network) {
    mixed = applyNetworkImpairment(mixed, workSr, rung.network, seed);
  }

  const at8k = await resampleFloat32(mixed, workSr, codecSr);
  const codecked = muLawRoundTripArray(at8k);

  return {
    id: rung.id,
    label: rung.label,
    snr_db: rung.snr_db,
    group: rung.group,
    samples: codecked,
    sr: codecSr,
  };
}

// Render every rung defined in conditions.json (the cafe SNR ladder, plus
// the network-impairment rungs) for one input recording. `bedAtWorkSr` is
// the pre-decoded cafe bed, already resampled to work_sr.
export async function renderLadder(inputAudioBuffer, conditionsData, bedAtWorkSr, onProgress) {
  const { defaults, rungs, network_rungs: networkRungs = [] } = conditionsData;
  const workSr = defaults.work_sr;
  const codecSr = defaults.codec_sr;

  const monoNative = toMonoFloat32(inputAudioBuffer);
  const monoWork = await resampleFloat32(monoNative, inputAudioBuffer.sampleRate, workSr);
  const leveled = setActiveLevel(monoWork, workSr, defaults.speech_level_dbov);
  const speechOnlyDb = rmsDbfs(leveled);

  const padded = padSilence(leveled, workSr, defaults.pad_head_s, defaults.pad_tail_s);

  const allRungs = [
    ...rungs.map((r) => ({ ...r, group: 'noise' })),
    ...networkRungs.map((r) => ({ ...r, group: 'network' })),
  ];

  const results = [];
  for (let i = 0; i < allRungs.length; i++) {
    const rung = allRungs[i];
    if (onProgress) onProgress({ stage: 'render', rung, index: i, total: allRungs.length });
    const result = await renderRung(
      padded, speechOnlyDb, rung, bedAtWorkSr, workSr, codecSr, defaults.seed_base + i,
    );
    results.push(result);
  }
  return results;
}

export function float32ToAudioBuffer(samples, sr, audioCtx) {
  const buf = audioCtx.createBuffer(1, samples.length, sr);
  buf.copyToChannel(samples, 0);
  return buf;
}
