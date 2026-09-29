// Word-level diff of a rung's transcript against the clean transcript, used
// to highlight mismatched words and to pick the rung where things "broke".

function normalizeWord(w) {
  return w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');
}

function tokenize(text) {
  return (text || '').trim().split(/\s+/).filter(Boolean);
}

// Longest-common-subsequence alignment over normalized words; words in the
// hypothesis that are not part of the LCS are marked as mismatched.
export function diffWords(cleanText, hypText) {
  const clean = tokenize(cleanText);
  const hyp = tokenize(hypText);
  const cleanNorm = clean.map(normalizeWord);
  const hypNorm = hyp.map(normalizeWord);

  const n = cleanNorm.length;
  const m = hypNorm.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = cleanNorm[i - 1] === hypNorm[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  const matchedHyp = new Array(m).fill(false);
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (cleanNorm[i - 1] === hypNorm[j - 1]) {
      matchedHyp[j - 1] = true;
      i--; j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }

  const tokens = hyp.map((text, idx) => ({ text, matched: matchedHyp[idx] }));
  const mismatchCount = tokens.filter((t) => !t.matched).length;
  const mismatchRatio = m === 0 ? (n === 0 ? 0 : 1) : mismatchCount / m;
  return { tokens, mismatchCount, mismatchRatio, wordCount: m };
}

// Given rungs in descending-SNR order (clean first), find the first
// non-clean noise-ladder rung whose transcript diverges from clean. Network
// rungs (packet loss, jitter) are not part of the ordered ladder, so they
// are excluded here and reported separately.
export function findBreakPoint(rungResults) {
  for (const r of rungResults) {
    if (r.group !== 'noise') continue;
    if (r.snr_db === null || r.snr_db === undefined) continue;
    if (r.diff && r.diff.mismatchCount > 0) {
      return r;
    }
  }
  return null;
}
