import { toMonoFloat32, resampleFloat32, renderLadder, float32ToAudioBuffer } from './audio-pipeline.js';
import { drawWaveform } from './waveform.js';
import { diffWords, findBreakPoint } from './diff.js';
import { transcribe } from './asr.js';

const els = {
  dropzone: document.getElementById('dropzone'),
  fileInput: document.getElementById('file-input'),
  recordBtn: document.getElementById('record-btn'),
  recordLabel: document.getElementById('record-label'),
  inputStatus: document.getElementById('input-status'),
  inputPanel: document.getElementById('input-panel'),
  ladderPanel: document.getElementById('ladder-panel'),
  ladder: document.getElementById('ladder'),
  verdict: document.getElementById('verdict'),
  resetBtn: document.getElementById('reset-btn'),
};

const playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
let currentSource = null;

let conditionsData = null;
let bedAtWorkSr = null;
let mediaRecorder = null;
let recordedChunks = [];
let recording = false;

function setStatus(text) {
  els.inputStatus.textContent = text || '';
}

function setInputEnabled(enabled) {
  els.dropzone.classList.toggle('disabled', !enabled);
  els.dropzone.tabIndex = enabled ? 0 : -1;
  els.fileInput.disabled = !enabled;
  els.recordBtn.disabled = !enabled;
}

async function loadStaticData() {
  const [conditions, beds] = await Promise.all([
    fetch('data/conditions.json').then((r) => r.json()),
    fetch('data/beds.json').then((r) => r.json()),
  ]);
  conditionsData = conditions;

  const bed = beds.beds.find((b) => b.id === conditions.noise.id);
  const resp = await fetch(bed.file);
  const arrayBuffer = await resp.arrayBuffer();
  const decoded = await playbackCtx.decodeAudioData(arrayBuffer);
  const mono = toMonoFloat32(decoded);
  bedAtWorkSr = await resampleFloat32(mono, decoded.sampleRate, conditions.defaults.work_sr);
}

// Guards against the file/mic input firing before the noise bed has
// finished loading (audio-pipeline.js needs bedAtWorkSr to be non-null).
const staticDataPromise = loadStaticData();

function decodeFileToBuffer(arrayBuffer) {
  // decodeAudioData detaches/consumes the buffer in some browsers, so callers
  // should pass a fresh copy if they need the original bytes afterward.
  return playbackCtx.decodeAudioData(arrayBuffer);
}

async function handleInputAudioBuffer(audioBuffer) {
  els.inputPanel.hidden = true;
  els.ladderPanel.hidden = false;
  els.verdict.hidden = true;
  els.ladder.innerHTML = '';

  const rungResults = await renderLadder(audioBuffer, conditionsData, bedAtWorkSr, (p) => {
    setStatus(`Rendering ${p.rung.label}… (${p.index + 1}/${p.total})`);
  });
  setStatus('');

  const rowEls = rungResults.map((r) => buildRungRow(r));
  let sectionShown = null;
  rowEls.forEach(({ el }, i) => {
    const group = rungResults[i].group;
    if (group !== sectionShown) {
      els.ladder.appendChild(buildSectionHeader(group));
      sectionShown = group;
    }
    els.ladder.appendChild(el);
  });

  let cleanText = '';
  for (let i = 0; i < rungResults.length; i++) {
    const r = rungResults[i];
    const { transcriptEl } = rowEls[i];
    transcriptEl.textContent = i === 0 ? 'Transcribing…' : 'Waiting…';
  }

  for (let i = 0; i < rungResults.length; i++) {
    const r = rungResults[i];
    const { transcriptEl } = rowEls[i];
    transcriptEl.textContent = 'Transcribing…';
    try {
      const text = await transcribe(r.samples, r.sr, resampleFloat32, (p) => {
        if (p.stage === 'model-load' && p.status === 'progress') {
          const pct = p.progress ? Math.round(p.progress) : 0;
          setStatus(`Loading speech-recognition model on ${p.device}… ${pct}%`);
        } else if (p.stage === 'model-load') {
          setStatus(`Loading speech-recognition model on ${p.device}…`);
        }
      });
      r.transcript = text;
      if (i === 0) {
        cleanText = text;
        transcriptEl.textContent = text || '(silence)';
        transcriptEl.classList.add('clean-text');
      } else {
        const diff = diffWords(cleanText, text);
        r.diff = diff;
        renderTranscriptDiff(transcriptEl, diff);
        if (r.group === 'network') {
          const badge = document.createElement('div');
          badge.className = diff.mismatchCount > 0 ? 'network-badge bad' : 'network-badge ok';
          badge.textContent = diff.mismatchCount > 0 ? '✗ transcript broke' : '✓ held up';
          transcriptEl.after(badge);
        }
      }
    } catch (err) {
      console.error('transcription failed', err);
      transcriptEl.textContent = 'Transcription failed.';
    }
    setStatus('');
  }

  const breakRung = findBreakPoint(rungResults);
  els.verdict.hidden = false;
  els.verdict.textContent = breakRung
    ? `On the noise ladder, it broke around ${breakRung.label} — that's where the transcript first went wrong.`
    : 'On the noise ladder, the transcript held up all the way down to 0 dB.';
}

function renderTranscriptDiff(el, diff) {
  el.textContent = '';
  if (diff.tokens.length === 0) {
    el.textContent = '(silence)';
    return;
  }
  diff.tokens.forEach((tok, idx) => {
    const span = document.createElement('span');
    span.textContent = tok.text;
    if (!tok.matched) span.classList.add('mismatch');
    el.appendChild(span);
    if (idx < diff.tokens.length - 1) el.appendChild(document.createTextNode(' '));
  });
}

function buildSectionHeader(group) {
  const h = document.createElement('h2');
  h.className = 'ladder-section';
  h.textContent = group === 'network'
    ? 'Network conditions (café at 20 dB)'
    : 'Café noise ladder';
  return h;
}

function buildRungRow(rung) {
  const row = document.createElement('div');
  row.className = 'rung';

  const header = document.createElement('div');
  header.className = 'rung-header';

  const label = document.createElement('span');
  label.className = 'rung-label';
  label.textContent = rung.label;

  const playBtn = document.createElement('button');
  playBtn.className = 'rung-play';
  playBtn.type = 'button';
  playBtn.textContent = '▶';
  playBtn.addEventListener('click', () => playRung(rung));

  const canvas = document.createElement('canvas');
  canvas.className = 'rung-waveform';

  header.appendChild(label);
  header.appendChild(playBtn);
  header.appendChild(canvas);

  const transcriptEl = document.createElement('div');
  transcriptEl.className = 'rung-transcript';

  row.appendChild(header);
  row.appendChild(transcriptEl);

  requestAnimationFrame(() => drawWaveform(canvas, rung.samples));

  return { el: row, transcriptEl, playBtn };
}

function playRung(rung) {
  if (currentSource) {
    try { currentSource.stop(); } catch (e) { /* already stopped */ }
  }
  if (playbackCtx.state === 'suspended') playbackCtx.resume();
  const buffer = float32ToAudioBuffer(rung.samples, rung.sr, playbackCtx);
  const src = playbackCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(playbackCtx.destination);
  src.start(0);
  currentSource = src;
}

function resetToInput() {
  els.inputPanel.hidden = false;
  els.ladderPanel.hidden = true;
  els.fileInput.value = '';
  setStatus('');
}

// -- input wiring -----------------------------------------------------------

els.dropzone.addEventListener('click', () => els.fileInput.click());
els.dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') els.fileInput.click();
});
els.dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  els.dropzone.classList.add('dragover');
});
els.dropzone.addEventListener('dragleave', () => els.dropzone.classList.remove('dragover'));
els.dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  els.dropzone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) loadFile(file);
});
els.fileInput.addEventListener('change', () => {
  const file = els.fileInput.files[0];
  if (file) loadFile(file);
});

async function loadFile(file) {
  setStatus('Decoding audio…');
  try {
    const arrayBuffer = await file.arrayBuffer();
    const audioBuffer = await decodeFileToBuffer(arrayBuffer);
    await staticDataPromise;
    await handleInputAudioBuffer(audioBuffer);
  } catch (err) {
    console.error(err);
    setStatus('Could not decode that file — try a WAV or MP3.');
  }
}

els.recordBtn.addEventListener('click', async () => {
  if (!recording) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) recordedChunks.push(e.data);
      };
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
        setStatus('Decoding your recording…');
        try {
          const arrayBuffer = await blob.arrayBuffer();
          const audioBuffer = await decodeFileToBuffer(arrayBuffer);
          await staticDataPromise;
          await handleInputAudioBuffer(audioBuffer);
        } catch (err) {
          console.error(err);
          setStatus('Could not process that recording.');
        }
      };
      mediaRecorder.start();
      recording = true;
      els.recordBtn.classList.add('recording');
      els.recordLabel.textContent = 'Stop recording';
      setStatus('Recording…');
    } catch (err) {
      console.error(err);
      setStatus('Microphone access was denied.');
    }
  } else {
    mediaRecorder.stop();
    recording = false;
    els.recordBtn.classList.remove('recording');
    els.recordLabel.textContent = 'Record from your mic';
  }
});

els.resetBtn.addEventListener('click', resetToInput);

// -- boot ---------------------------------------------------------------

setInputEnabled(false);
setStatus('Loading demo assets…');
staticDataPromise
  .then(() => {
    setInputEnabled(true);
    setStatus('');
  })
  .catch((err) => {
    console.error('failed to load demo assets', err);
    setStatus('Failed to load demo assets — see console.');
  });
