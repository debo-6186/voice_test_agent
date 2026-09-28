# Responsible use

This tool places automated calls against commercial services and produces numbers
that can affect purchasing decisions. Both of those carry obligations.

## Before you run it

**Check the vendor's terms of service.** Automated or synthetic calling against a
vendor endpoint may be restricted, rate-limited or prohibited. It is your account
and your relationship with them. We cannot check this for you and the defaults here
are not legal advice.

**Only test agents you own or are authorised to test.** Pointing this at a third
party's production phone number is not benchmarking — it is unsolicited automated
calling, it may be illegal in your jurisdiction, and it will get the number blocked.
The tool will not stop you; your conscience and your lawyer should.

**Never commit credentials.** Keys come from the environment or `.env`, which is in
`.gitignore`. Nothing in `vendors/*.yaml` or any run artifact should ever contain a
key — `polycall doctor` checks for accidental key material in committed config and
will fail loudly.

## Cost guards

`polycall bench --all` can spend thousands of dollars in about two hours. The
defaults are deliberately cautious:

- Every run prints an **estimated cost** and requires confirmation before dialling.
- `--yes` is required to skip the prompt, and is the only way to run in CI.
- `--max-spend` is enforced client-side: the run aborts when projected spend exceeds
  it, mid-sweep if necessary.
- Concurrency defaults to **4 per vendor**, well below every vendor's limit.
  Raising it is explicit: `--concurrency 20`.
- A dry run (`--dry-run`) walks the whole matrix, prints the plan and the cost, and
  places no calls.

Set `POLYCALL_MAX_SPEND` in your shell if you want a hard ceiling you cannot
forget to pass.

## Do not hammer a vendor

Concurrency limits exist for capacity reasons. Running at a vendor's ceiling for
hours is indistinguishable from a load test they did not agree to.

- Stay within your plan's published concurrency. The tool reads it from
  `vendors/*.yaml` and refuses to exceed it without `--force-concurrency`.
- Exponential backoff on 429 is on by default and cannot be disabled.
- If you are running the full matrix repeatedly, tell the vendor. Most will be
  helpful, and several publish a process for raising limits.

## If you publish comparative results

The value of this project depends on the results being defensible rather than
inflammatory. If you are going to publish vendor comparisons:

1. **Use Mode B.** Mode A measures *your agent* on that vendor. Publishing a Mode A
   number as a vendor score is wrong, and `polycall report` will not generate a
   leaderboard-comparable table from one.
2. **Publish the corpus and the configs**, not just the scores. Include the agent
   spec, the seeds, the manifest hashes, and the discarded turns with reasons.
   A result nobody can reproduce is an opinion.
3. **Publish your instrument's error.** Per-language referee WER, the significance
   floor, and the SNR at which you truncated the ladder. Results below the floor are
   reported as `n.s.`, never ranked.
4. **Give vendors right of reply.** Send them their numbers and the method with a
   reasonable window — two weeks is customary — before publishing. Publish their
   response alongside. This is the single thing that separates a benchmark from a
   hit piece, and it costs you nothing.
5. **Date and version everything.** Vendor stacks change weekly. A number without a
   date and a commit hash is meaningless within a month.
6. **State what you did not measure.** Non-connecting calls stay in the denominator
   with reasons. Say which vendors you excluded and why.

## Known limitations to disclose

Anyone publishing from this tool should carry these forward:

- **Synthetic speech is easier than human speech.** TTS is cleaner and more uniform,
  so absolute scores are optimistic. Rankings and deltas hold; absolute numbers do
  not transfer to human callers without the correction factor in `docs/referees.md`.
- **The referee degrades too.** Our measuring instrument loses accuracy under the
  same noise. Below the published truncation point, results are not interpretable.
- **Agents are non-deterministic.** Single runs are not evidence. Report pass^k with
  confidence intervals, never a single number.
- **`en-SG` is not supported by seven of eight vendors.** Singapore English results
  reflect whatever fallback the vendor chose; the manifest records which one.

## Privacy

The corpus is synthetic — invented names, invented numbers. If you author your own
scenarios, do not put real customer data in them. Recordings of your own agent's
responses may contain whatever your agent says; treat run artifacts accordingly and
do not publish them without checking.
