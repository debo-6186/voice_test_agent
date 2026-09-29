// Minimal min/max-per-column waveform renderer.

export function drawWaveform(canvas, samples, color = '#4a9eff') {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 300;
  const cssH = canvas.clientHeight || 48;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  if (!samples || samples.length === 0) return;

  const mid = cssH / 2;
  const step = Math.max(1, Math.floor(samples.length / cssW));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x < cssW; x++) {
    const start = x * step;
    const end = Math.min(samples.length, start + step);
    let min = 1;
    let max = -1;
    for (let i = start; i < end; i++) {
      const v = samples[i];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    if (start >= samples.length) break;
    const y1 = mid - max * mid;
    const y2 = mid - min * mid;
    ctx.moveTo(x + 0.5, y1);
    ctx.lineTo(x + 0.5, y2);
  }
  ctx.stroke();
}
