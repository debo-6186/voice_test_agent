// transformers.js wrapper around whisper-tiny, loaded lazily from a CDN so
// the page stays small until someone actually drops in audio. Prefers
// WebGPU, falls back to WASM - both work, WASM is just slower (per
// docs/stack.md: "degrade gracefully without WebGPU").

const TRANSFORMERS_URL =
  'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.2.4/dist/transformers.min.js';
const MODEL_ID = 'Xenova/whisper-tiny';
const WHISPER_SR = 16000;

let transcriberPromise = null;

// navigator.gpu existing does not mean a GPU adapter is actually available
// (true in several headless/sandboxed/CI browsers) - request one to find out.
async function hasWorkingWebGpu() {
  if (typeof navigator === 'undefined' || !navigator.gpu) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return !!adapter;
  } catch {
    return false;
  }
}

async function loadTranscriber(onProgress) {
  const { pipeline, env } = await import(TRANSFORMERS_URL);
  env.allowLocalModels = false;
  // Static hosts like GitHub Pages cannot send the COOP/COEP headers that
  // SharedArrayBuffer (and therefore multi-threaded WASM) require. Force
  // single-threaded WASM so the fallback path works with no special headers.
  env.backends.onnx.wasm.numThreads = 1;

  const device = (await hasWorkingWebGpu()) ? 'webgpu' : 'wasm';
  if (onProgress) onProgress({ stage: 'model-load', device, progress: 0 });

  try {
    return await pipeline('automatic-speech-recognition', MODEL_ID, {
      device,
      progress_callback: (p) => {
        if (onProgress) onProgress({ stage: 'model-load', device, ...p });
      },
    });
  } catch (err) {
    if (device === 'webgpu') {
      // WebGPU adapter existed but pipeline creation still failed - fall
      // back to WASM rather than leaving the whole page broken.
      return await pipeline('automatic-speech-recognition', MODEL_ID, {
        device: 'wasm',
        progress_callback: (p) => {
          if (onProgress) onProgress({ stage: 'model-load', device: 'wasm', ...p });
        },
      });
    }
    throw err;
  }
}

export function getTranscriber(onProgress) {
  if (!transcriberPromise) {
    transcriberPromise = loadTranscriber(onProgress).catch((err) => {
      transcriberPromise = null; // allow a retry on the next call
      throw err;
    });
  }
  return transcriberPromise;
}

// `resampleFn` is injected (rather than imported) to avoid this module
// depending on the WebAudio-specific resampler when only text output matters
// in tests; in the app it is audio-pipeline.js's resampleFloat32.
export async function transcribe(samples, sr, resampleFn, onProgress) {
  const transcriber = await getTranscriber(onProgress);
  const at16k = sr === WHISPER_SR ? samples : await resampleFn(samples, sr, WHISPER_SR);
  const result = await transcriber(at16k, { chunk_length_s: 30 });
  return (Array.isArray(result) ? result[0] : result).text.trim();
}
