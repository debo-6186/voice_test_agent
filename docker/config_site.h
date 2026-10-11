/* Custom PJSIP build config - see docs/sip-harness.md.
 *
 * The ceiling is conference ports, not calls. MAX_PLAYERS/MAX_RECORDERS
 * default to 32 each and every scripted call needs ~3 slots - raising
 * MAX_CALLS alone leaves you stuck at 32.
 *
 * Shipped as a real file and COPYed into the Dockerfile rather than
 * written via a RUN heredoc: Dockerfile heredocs are a BuildKit-frontend
 * feature, and on a builder without it (e.g. `docker.io`'s bundled engine
 * with no buildx plugin, which silently falls back to the legacy builder)
 * the heredoc produces an EMPTY file with no error - quietly reverting
 * every limit below back to PJSIP's stock defaults.
 */
#define PJSUA_MAX_CALLS            64
#define PJSUA_MAX_PLAYERS          128
#define PJSUA_MAX_RECORDERS        128
#define PJSUA_MAX_CONF_PORTS       512
#define PJ_IOQUEUE_MAX_HANDLES     256
#define PJSIP_MAX_TSX_COUNT        (640*1024-1)
#define PJSIP_MAX_DIALOG_COUNT     (640*1024-1)
#define PJMEDIA_CONF_BACKEND       PJMEDIA_CONF_PARALLEL_BRIDGE_BACKEND
#define PJMEDIA_CONF_THREADS       4
