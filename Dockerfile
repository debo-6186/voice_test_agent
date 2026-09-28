# PolyCall Bench — PJSIP prebuilt, because the custom config_site.h is the single
# biggest barrier to anyone running a live call.
FROM python:3.11-slim AS pjsip

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential swig pkg-config libasound2-dev \
      libssl-dev libopus-dev libgsm1-dev libspeex-dev \
      curl ca-certificates && rm -rf /var/lib/apt/lists/*

ARG PJ_VERSION=2.16
RUN curl -sSL https://github.com/pjsip/pjproject/archive/refs/tags/${PJ_VERSION}.tar.gz \
    | tar xz -C /tmp

# The ceiling is conference ports, not calls. MAX_PLAYERS/MAX_RECORDERS default to
# 32 each and every scripted call needs ~3 slots — raising MAX_CALLS alone leaves
# you stuck at 32. See docs/sip-harness.md.
RUN cat > /tmp/pjproject-${PJ_VERSION}/pjlib/include/pj/config_site.h <<'CFG'
#define PJSUA_MAX_CALLS            64
#define PJSUA_MAX_PLAYERS          128
#define PJSUA_MAX_RECORDERS        128
#define PJSUA_MAX_CONF_PORTS       512
#define PJ_IOQUEUE_MAX_HANDLES     256
#define PJSIP_MAX_TSX_COUNT        (640*1024-1)
#define PJSIP_MAX_DIALOG_COUNT     (640*1024-1)
#define PJMEDIA_CONF_BACKEND       PJMEDIA_CONF_PARALLEL_BRIDGE_BACKEND
#define PJMEDIA_CONF_THREADS       4
CFG

WORKDIR /tmp/pjproject-${PJ_VERSION}
RUN ./configure --enable-shared --disable-video --disable-sdl --disable-ffmpeg \
      CFLAGS="-O2 -fPIC" && make dep && make -j"$(nproc)" && make install && ldconfig
RUN cd pjsip-apps/src/swig/python && make && make install

# ---------------------------------------------------------------------------
FROM python:3.11-slim
COPY --from=pjsip /usr/local/lib /usr/local/lib
COPY --from=pjsip /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg espeak-ng libopus0 libgsm1 libspeex1 libssl3 \
    && rm -rf /var/lib/apt/lists/* && ldconfig

# Workers are sharded across processes; each opens its own media ports.
RUN echo "* soft nofile 65535" >> /etc/security/limits.conf

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir -e .

ENV POLYCALL_DEVICE=auto
ENTRYPOINT ["polycall"]
CMD ["doctor"]
