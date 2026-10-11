// Small seeded PRNG (mulberry32) so bed-offset selection is deterministic
// given the same seed - "everything seeded", per project convention, even
// though this JS implementation does not need to match Python bit-for-bit.

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Pick a deterministic start offset into a long bed so a `lengthSamples`
// slice fits, and loop-with-crossfade if the bed is shorter than needed.
export function sliceBed(bedSamples, lengthSamples, rng, crossfadeSamples = 2048) {
  const n = bedSamples.length;
  if (n >= lengthSamples) {
    const maxStart = n - lengthSamples;
    const start = Math.floor(rng() * (maxStart + 1));
    return bedSamples.slice(start, start + lengthSamples);
  }
  // Loop the bed with a short crossfade at each seam.
  const out = new Float32Array(lengthSamples);
  const xf = Math.min(Math.floor(n / 4), crossfadeSamples);
  let pos = 0;
  while (pos < lengthSamples) {
    const take = Math.min(n, lengthSamples - pos);
    if (pos > 0 && xf > 0 && take > xf) {
      for (let i = 0; i < xf; i++) {
        const ramp = i / xf;
        out[pos + i] = out[pos + i] * (1 - ramp) + bedSamples[i] * ramp;
      }
      for (let i = xf; i < take; i++) out[pos + i] = bedSamples[i];
    } else {
      for (let i = 0; i < take; i++) out[pos + i] = bedSamples[i];
    }
    pos += take - (take === n ? xf : 0);
  }
  return out;
}
