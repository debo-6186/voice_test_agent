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

# Shipped as a real file (docker/config_site.h) rather than written via a
# RUN heredoc: heredocs are a BuildKit-frontend feature, and on a builder
# without it (e.g. `docker.io`'s bundled engine with no buildx plugin,
# which silently falls back to the legacy builder) a heredoc produces an
# EMPTY file with no error - quietly reverting every limit in it back to
# PJSIP's stock defaults (e.g. stuck at the default 32 conference ports)
# with nothing in the build log to suggest why. A plain COPY works
# identically on every Docker version.
COPY docker/config_site.h /tmp/pjproject-${PJ_VERSION}/pjlib/include/pj/config_site.h

WORKDIR /tmp/pjproject-${PJ_VERSION}
RUN ./configure --enable-shared --disable-video --disable-sdl --disable-ffmpeg \
      CFLAGS="-O2 -fPIC" && make dep && make -j"$(nproc)" && make install && ldconfig
RUN cd pjsip-apps/src/swig/python && make && make install
# `make install` above lands under the builder's user-site
# (/root/.local/...), inside a legacy .egg directory - not under /usr/local,
# so it would be missed by the final stage's COPY below. Flatten the egg's
# contents (pjsua2.py, _pjsua2*.so) into site-packages here, with a plain
# shell glob (reliable) rather than a cross-stage COPY wildcard (BuildKit
# does not resolve a `*` earlier in a COPY --from path the way you'd expect -
# it silently matches nothing instead of erroring, until the COPY itself
# fails with "no source files were specified").
RUN cp -r /root/.local/lib/python3.11/site-packages/pjsua2-*.egg/* \
       /usr/local/lib/python3.11/site-packages/

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
