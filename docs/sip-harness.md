# SIP harness — PJSIP build and architecture

PJSUA2 is the only client that does codec negotiation, WAV playback and WAV
recording in one scriptable process. **Do not use SIPp** — no WAV support, no codec
negotiation; it replays RTP blindly and will send the wrong payload type when the
agent picks a different codec. `baresip` (BSD-3, `aufile` module, scriptable over
`ctrl_tcp`) is a reasonable second choice.

## The build

`PJSUA_MAX_CALLS` defaults to **4** and cannot be raised at runtime — the docstring
is explicit that the library must be recompiled.

**But the real ceiling is conference ports, not calls.** Every scripted call needs
roughly three conference slots: one for the call, one for the WAV player, one for
the recorder. `PJSUA_MAX_PLAYERS` and `PJSUA_MAX_RECORDERS` default to **32 each**,
so raising only `PJSUA_MAX_CALLS` leaves you mysteriously stuck at 32.

```c
/* config_site.h — rebuild pjproject 2.16+ with this */
#define PJSUA_MAX_CALLS            64
#define PJSUA_MAX_PLAYERS          128   /* default 32 — the real cap */
#define PJSUA_MAX_RECORDERS        128   /* default 32 */
#define PJSUA_MAX_CONF_PORTS       512   /* >= 3 x calls, with headroom */
#define PJ_IOQUEUE_MAX_HANDLES     256   /* official guidance: ~3x calls */
#define PJSIP_MAX_TSX_COUNT        (640*1024-1)
#define PJSIP_MAX_DIALOG_COUNT     (640*1024-1)
#define PJMEDIA_CONF_BACKEND       PJMEDIA_CONF_PARALLEL_BRIDGE_BACKEND
#define PJMEDIA_CONF_THREADS       4     /* <= physical cores, not hyperthreads */
```

Also raise `ulimit -n` to at least 4× `PJ_IOQUEUE_MAX_HANDLES`.

## Architecture

**Shard across processes, not threads.** N processes × 10–15 calls each rather than
one process at 50. This:

- sidesteps PJSIP issue **#3125** entirely — a GIL deadlock between
  `Endpoint::libHandleEvents()` on the main thread and `Call::getInfo()` on a worker
  thread, commonly triggered by an f-string calling `getInfo()` inside a log
  statement. **Closed upstream as "not planned"; there is no fix.**
- isolates segfaults (one crash loses 15 calls, not 1,350)
- lets you pin each process to a core

If you must run threaded, the workaround is a global lock held by both
`libHandleEvents` and all `Call` callbacks, or `threadCnt=0` + `mainThreadOnly=True`
with periodic polling.

## Five traps

1. **`setNullDev()` in every worker.** With no sound device and no master port,
   nothing pumps the conference bridge, async ops sit forever, conference slots fill
   and are never freed, and the process dies after N calls. This is the single most
   common cause of "it worked for 30 calls then stopped".
2. **Hold explicit Python references** to every `Call`, `AudioMediaPlayer` and
   `AudioMediaRecorder` for its full lifetime. Python GC'ing a wrapper while PJSIP
   still holds the port is a segfault. Call `libDestroy()` explicitly on shutdown.
3. **`libRegisterThread()` once per long-lived thread.** Each call allocates
   thread-description memory freed only at `libDestroy()` — calling it per task in a
   pool leaks steadily across a long run.
4. **Player loops forever by default.** Pass `PJMEDIA_FILE_NO_LOOP` to
   `createPlayer`. And `stopTransmit` does **not** close a recorder's WAV — delete
   the recorder object to finalise the file.
5. **Use 2.16+.** Slot reuse after removing a player was broken before it (fixed by
   PR #4253's synchronous fast path). Wait for `onAudioMediaOpCompleted` before
   freeing buffers — the clock thread may still be using them.

## Capacity

PJSIP's own bench: 8 threads serving **240 concurrent conference ports** without
audio degradation — ~80 calls per process at 3 ports each. Treat as an upper bound;
lower values were not measured.

**Thread priority caveat:** only the get-frame thread attempts elevated priority, and
on Linux that needs `CAP_SYS_NICE`. On a typical non-root container nothing runs
elevated, `pj_thread_set_prio` fails silently at debug level, and media threads can
be preempted — which shows up as latency jitter. Size with margin.

PJSIP's performance guide explicitly says "do not use PJSUA-LIB" for thousands of
simultaneous calls; at 20–60 concurrent we are well inside its design envelope.
Note the simple GUID generator produces duplicates after ~2^14 generations — restart
the process between full sweeps.

## Media and pacing

- **Offer PCMU only.** One codec, every vendor, every call.
- Stream at **real-time pace**: 20 ms of audio every 20 ms. Blasting the socket makes
  endpointing and VAD behave completely differently and every latency number becomes
  fiction.
- Keep a **uniform clock rate** across all media components to avoid resampling cost.
- Record **both legs to a stereo tape** with per-turn timestamps. All latency and
  turn-taking metrics are derived from that tape, not from vendor-reported values.
- Turn boundary detection: inbound silence > 700 ms.
