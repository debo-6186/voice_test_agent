// ITU-T G.711 mu-law compress/expand, expressed as WaveShaperNode curves.
//
// mu-law is not a "real" codec in the sense of a black box - it is a single
// companding curve. A WaveShaperNode with oversample:"none" and a curve of
// exactly 256 points maps each input sample to one of 256 lookup values by
// index, which *is* the 8-bit mu-law quantization step. A second WaveShaper
// with the inverse curve is the decoder.

const MU = 255;
const MU_LAW_LEVELS = 256;

// Standard mu-law compression: sign(x) * ln(1 + mu*|x|) / ln(1 + mu), then
// quantized to 8 bits by discretizing input space into 256 buckets (which is
// exactly what a 256-point WaveShaper curve does for us).
function muLawCompress(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.min(Math.abs(x), 1);
  return sign * Math.log1p(MU * ax) / Math.log1p(MU);
}

function muLawExpand(y) {
  const sign = y < 0 ? -1 : 1;
  const ay = Math.min(Math.abs(y), 1);
  return sign * (Math.expm1(ay * Math.log1p(MU)) / MU);
}

// Build the 256-point encode curve: input index i in [0, 255] maps to
// x = -1 + 2*i/255, curve[i] = muLawCompress(x). Because the curve has only
// 256 points and oversample is "none", the WaveShaperNode necessarily
// quantizes every input sample to one of these 256 output values - that is
// the lossy part of G.711 mu-law.
export function buildEncodeCurve() {
  const curve = new Float32Array(MU_LAW_LEVELS);
  for (let i = 0; i < MU_LAW_LEVELS; i++) {
    const x = -1 + (2 * i) / (MU_LAW_LEVELS - 1);
    curve[i] = muLawCompress(x);
  }
  return curve;
}

// Build the inverse (decode/expand) curve. Its domain is the *output* of the
// encode curve, i.e. also effectively [-1, 1], so we sample muLawExpand at
// the same 256 evenly spaced points.
export function buildDecodeCurve() {
  const curve = new Float32Array(MU_LAW_LEVELS);
  for (let i = 0; i < MU_LAW_LEVELS; i++) {
    const y = -1 + (2 * i) / (MU_LAW_LEVELS - 1);
    curve[i] = muLawExpand(y);
  }
  return curve;
}

// Apply the mu-law round trip to a plain Float32Array (used as a fallback /
// for the offline mix stage where we are not routing through a WaveShaper).
export function muLawRoundTripArray(samples) {
  const enc = buildEncodeCurve();
  const dec = buildDecodeCurve();
  const out = new Float32Array(samples.length);
  const n = MU_LAW_LEVELS - 1;
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    const idx = Math.round(((x + 1) / 2) * n);
    const compressed = enc[idx];
    const idx2 = Math.round(((compressed + 1) / 2) * n);
    out[i] = dec[idx2];
  }
  return out;
}
