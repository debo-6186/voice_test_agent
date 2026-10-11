// JS port of the subset of vqlab/audio.py needed by the demo: plain dBFS RMS,
// a P.56-ish active speech level estimate, and the SNR-mixing gain formula.
// These mirror the Python definitions so "10 dB" means the same thing in both
// places, even though the two implementations are otherwise independent.

const EPS = 1e-12;

export function dbToLin(db) {
  return Math.pow(10, db / 20);
}

export function linToDb(v) {
  return 20 * Math.log10(Math.max(Math.abs(v), EPS));
}

export function rmsDbfs(x) {
  if (x.length === 0) return -120;
  let sum = 0;
  for (let i = 0; i < x.length; i++) sum += x[i] * x[i];
  const meanSq = sum / x.length;
  return 10 * Math.log10(meanSq + EPS);
}

// ITU-T P.56-style active speech level: envelope-follow with a 30 ms time
// constant, then find the energy threshold whose above-threshold segments'
// RMS is self-consistent (mirrors vqlab.audio.active_speech_level).
export function activeSpeechLevel(x, sr, marginDb = 15.9) {
  if (x.length === 0) return { levelDbov: -120, activity: 0 };
  const g = Math.exp(-1 / (sr * 0.03));
  let q = 0;
  const env = new Float64Array(x.length);
  for (let i = 0; i < x.length; i++) {
    const a = Math.abs(x[i]);
    q = (1 - g) * a + g * q;
    env[i] = q;
  }
  let sumSq = 0;
  for (let i = 0; i < x.length; i++) sumSq += x[i] * x[i];
  const longTerm = Math.sqrt(sumSq / x.length) + EPS;

  let best = { levelDbov: -120, activity: 0 };
  for (let k = -20; k <= 0; k++) {
    const t = longTerm * Math.pow(2, k);
    let n = 0;
    let sumActSq = 0;
    for (let i = 0; i < x.length; i++) {
      if (env[i] > t) {
        n++;
        sumActSq += x[i] * x[i];
      }
    }
    if (n < Math.max(8, 0.005 * x.length)) continue;
    const aLvl = 10 * Math.log10(sumActSq / n + EPS);
    const af = n / x.length;
    best = { levelDbov: aLvl, activity: af };
    if (aLvl - 20 * Math.log10(t + EPS) > marginDb) break;
  }
  return best;
}

// Scale `samples` in place-equivalent (returns a new array) so the active
// speech level sits at targetDbov.
export function setActiveLevel(samples, sr, targetDbov) {
  const { levelDbov } = activeSpeechLevel(samples, sr);
  const gain = dbToLin(targetDbov - levelDbov);
  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gain;
  return out;
}

// Same definition as vqlab/corpus.py's SNR mixing: plain dBFS RMS of the
// clean speech vs. the noise bed slice, gain solved so the delivered ratio
// equals snrDb.
export function snrMixGain(speechSamples, noiseSamples, snrDb) {
  const sDb = rmsDbfs(speechSamples);
  const nDb = rmsDbfs(noiseSamples);
  return dbToLin(sDb - snrDb - nDb);
}
