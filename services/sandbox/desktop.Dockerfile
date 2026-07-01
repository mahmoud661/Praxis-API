# Base image for LOCAL sandbox containers (SANDBOX_PROVIDER=local).
#
# It is NOT the sandbox-service itself — sandbox-service launches sibling
# containers FROM this image on the host Docker daemon. It bundles a minimal
# X stack + VNC server so the workspace "Sandbox" tab can show a real, live
# desktop (noVNC in the browser), plus python for the agent's code tools.
#
#   Xvfb        virtual framebuffer (headless X display :99)
#   fluxbox     tiny window manager
#   xterm       a visible terminal so the desktop isn't blank
#   x11vnc      VNC server exporting display :99 on TCP 5900 (the sandbox
#               service relays the browser's WebSocket to this)
#   x11-utils   xdpyinfo — used to wait for the display to come up
#
# Built via the `sandbox-desktop-image` compose service; sandbox-service
# waits for that build to finish before it starts.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb \
      x11-utils \
      x11vnc \
      xkb-data \
      fluxbox \
      xterm \
      procps \
      ca-certificates \
      curl \
      git \
    && rm -rf /var/lib/apt/lists/*

# xkb-data: XKEYBOARD keymaps, required for x11vnc's `-xkb` (full keyboard).

# --- Docker engine + compose (nested, runs under Sysbox) --------------------
# Lets the sandbox run `docker` / `docker compose` inside itself. dockerd is
# started on demand by sandbox-service. Only actually runnable when the
# sandbox launches under the Sysbox runtime (SANDBOX_RUNTIME=sysbox-runc);
# under plain runc dockerd won't start (needs privileges Sysbox provides
# safely). Installed from Docker's official apt repo for the compose plugin.
# Versions pinned for Sysbox compatibility: runc >= 1.2 added an openat2
# RESOLVE_NO_XDEV procfs check that rejects Sysbox's synthetic /proc/sys
# ("unsafe procfs detected: invalid cross-device link"). So install Docker 27
# + containerd 1.7, then REPLACE the bundled runc (1.3.x) with 1.1.x, which
# predates that check and works under Sysbox. (amd64 only for now.)
ARG DOCKER_VERSION=5:27.5.1-1~debian.12~bookworm
ARG CONTAINERD_VERSION=1.7.29-1~debian.12~bookworm
ARG RUNC_VERSION=v1.1.15
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce=${DOCKER_VERSION} docker-ce-cli=${DOCKER_VERSION} \
        containerd.io=${CONTAINERD_VERSION} docker-compose-plugin \
    && curl -fsSL -o /usr/bin/runc \
        "https://github.com/opencontainers/runc/releases/download/${RUNC_VERSION}/runc.amd64" \
    && chmod +x /usr/bin/runc \
    && rm -rf /var/lib/apt/lists/*

# Every exec'd shell (the VNC/X stack, user commands) targets the headless
# display the client starts on :99.
ENV DISPLAY=:99

# CMD is irrelevant: sandbox-service overrides it with `sleep infinity` to
# keep the container alive, then boots the X + VNC stack lazily.
