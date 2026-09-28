# Statistics

Voice agents are non-deterministic and you cannot fix that. In published work,
temperature 0 produced **80 unique outputs from 1,000 completions** of the same
prompt — the cause is batch-invariance in inference kernels, not sampling. OpenAI's
`seed` is explicitly best-effort ("mostly deterministic"; track `system_fingerprint`
and treat a change as a broken experiment). No voice platform exposes a determinism
guarantee, and realtime pipelines add VAD timing, barge-in races and jitter on top.

**Plan for repeats; do not chase determinism.**

## pass^k, not pass@k

Two opposite metrics with near-identical names.

- **pass@k** = P(at least one of k succeeds) — *capability*.
  `E[1 − C(n−c,k)/C(n,k)]`. **Wrong here**: it rises with k and hides degradation.
  Only meaningful when you can verify and select the winner. A voice agent cannot
  self-verify mid-call.
- **pass^k** = P(all k succeed) — *reliability*. `E[C(c,k)/C(n,k)]`.
  "A support agent does not get ten attempts at one customer. It gets one attempt
  each at ten customers."

Key property: under independence **pass^k = p^k**, so pass^k *amplifies* the
degradation signal.

| p | k=1 | k=2 | k=3 | k=5 | k=8 |
|---|---|---|---|---|---|
| 0.95 | 0.950 | 0.902 | 0.857 | 0.774 | 0.663 |
| 0.90 | 0.900 | 0.810 | 0.729 | 0.590 | 0.430 |
| 0.80 | 0.800 | 0.640 | 0.512 | 0.328 | 0.168 |
| 0.70 | 0.700 | 0.490 | 0.343 | 0.168 | 0.058 |

**Diagnostic worth reporting:** the deviation of empirical pass^k from p^k measures
task-level heterogeneity. pass^k ≫ p^k means failures concentrate on a hard subset
(deterministic difficulty); pass^k ≈ p^k means pure stochastic flakiness. Noise
should shift you toward the latter — report that shift as a finding.

## Pairing — a free 2.5× saving

Run the **same scenario** at every SNR level rather than independent scenarios per
level, then analyse paired (McNemar).

| Design | n needed (10-pt effect, 80% power, α=0.05) |
|---|---|
| Unpaired two-proportion, 90% vs 80% | 199/arm = **398 total** |
| **Paired (McNemar)** | **155 pairs** |

Precision of a single estimate (95% Wilson CI at p≈0.8):

| n | half-width |
|---|---|
| 50 | ±10.9 pp |
| 100 | ±7.8 pp |
| 200 | ±5.5 pp |
| 500 | ±3.5 pp |
| 1,000 | ±2.5 pp |

## The analysis stack

1. **Cluster bootstrap at task level, conditions kept paired.** 10,000 resamples;
   when a task is sampled, all six of its SNR levels travel together. This is
   τ-Elicitation's method. Ignoring the clustering understates uncertainty by up
   to **3×**.
2. **Wilson or Clopper-Pearson intervals, never Wald.** Wald is badly miscalibrated
   near 0 and 1 — exactly where the extreme SNR levels sit.
3. **Sign-permutation tests**, 100,000 permutations, **Holm correction** for the
   many pairwise comparisons across languages and vendors.
4. **Psychometric function with guess AND lapse rates:**
   `p = γ + (1 − γ − λ)·F(SNR)`. Omitting the lapse rate is the classic error and
   biases both threshold and slope. The clean condition estimates λ for free.
5. **Threshold = −β₀/β₁ with Fieller or bootstrap CIs**, not the delta method —
   the threshold is a ratio of parameters and delta-method normality fails when the
   slope is imprecise. Cross-check against non-parametric **Spearman-Kärber**;
   material disagreement means the parametric form is wrong.
6. **Fit with `psignifit`** (Wichmann lab) — Bayesian, handles the overdispersion
   your data will have because tasks differ in difficulty. `getThreshold(res,
   pCorrect, unscaled)` returns threshold + credible interval at any criterion.
   Note its CIs are conservative, increasingly so away from the specified level.
7. **Choose repeats K by `E[σ²ᵢ]/K ≪ Var(x)`.** Past that point extra repeats buy
   essentially nothing and the budget should go to more *scenarios*.

## Design requirements

**The ladder must bracket the full range.** Probit design theory wants 5–8 levels
spanning ~0% to ~100%, most points between 10% and 90%. Six is right — but if all
six land between 70% and 95% you cannot estimate the slope and the threshold CI will
be unbounded.

→ **Pilot with an adaptive staircase (QUEST/ZEST) to locate the threshold region**,
then place the fixed ladder to bracket it. QUEST reaches 84% efficiency after 128
trials vs PEST's ~45%; ZEST's mean estimator hits SD 0.110 log units after 8 trials
(mode-QUEST needs 20 to do worse). Adaptive gives you a *threshold*, not a *curve* —
use it for the pilot, the fixed paired ladder for the published result.

Caveats on adaptive methods: no published precedent in voice-agent evaluation; QUEST
assumes a known fixed slope (use QUEST+ or psi to estimate jointly); classical
staircases assume trial independence and a stable threshold, which agent drift
violates; and sequential dependency means bootstrap CIs need care — bootstrap the
*procedure*, or use the Bayesian posterior directly.

**Score at call level, not slot level.** Per-slot successes are correlated within a
call and violate binomial independence (the speech-intelligibility literature hit
exactly this with sentence-level scoring). Binary task success keeps the statistics
clean; report per-field accuracy separately as a descriptive.

## Optional: sequential testing

Group sequential testing with a pre-registered Pocock spending function reports
60–80% call reductions at 2–3 point CI tolerances (±3-point CI needs only ~15% of
samples; ±2-point ~30%). Worth adopting for repeat runs.

**Trap:** GST requires pre-registering the spending function and the number of
interim looks. Peeking at results repeatedly without that correction inflates Type I
error badly — the classic optional-stopping problem.

## Benchmarking context

Published agent benchmarks are mostly bad at this, which is an opportunity:

| Benchmark | Trials/task | CIs? |
|---|---|---|
| τ-bench | ≥3 (k to 8, some figures to 32) | No |
| τ²-bench | configurable, examples use 1 | No |
| Cekura Bench | 3 × 82 scenarios | No |
| ReliabilityBench | 2 | No |
| HAL (Princeton) | typically 1 | **Explicitly none** |
| **τ-Elicitation** | 3 environments × 200 tasks | **Yes — bootstrap + permutation** |

HAL's own admission: 21,730 rollouts, 9 benchmarks, ~$40,000, and *"we were forced
to rely on single runs without statistical validation for most evaluations."*

Magnitude of the pass^1 → pass^k drop in published work: τ-bench retail GPT-4o goes
61% → ~25% at pass^8. τ-Elicitation's scaffolded GPT goes 75% Pass@1 → 38.5%
cross-environment Pass³.

**Key references:** Miller, *Adding Error Bars to Evals* (arXiv 2411.00640) for CIs
and clustered SEs; τ-bench (arXiv 2406.12045) for pass^k; τ-Elicitation
(arXiv 2609.13602) for the bootstrap/permutation protocol; Watson & Pelli 1983 for
QUEST; Schütt et al. 2016 for psignifit.
