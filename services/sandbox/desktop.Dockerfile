# Base image for LOCAL sandbox containers (SANDBOX_PROVIDER=local).
#
# It is NOT the sandbox-service itself — sandbox-service launches sibling
# containers FROM this image on the host Docker daemon. It bundles a styled
# desktop + VNC server so the workspace "Sandbox" tab shows a live desktop
# (noVNC in the browser), plus python for the agent's code tools.
#
# Desktop stack (Firewatch-rice style: wallpaper, floating windows, widgets):
#   Xvfb       virtual framebuffer (headless X display :99)
#   openbox    floating window manager — normal, click-driven windows
#   tint2      slim dark top panel (taskbar + clock)
#   conky      system widgets on the right (time, cpu, mem, disk); pseudo-
#              transparency (root-pixmap copy) — NO compositor. picom/xrender
#              was tried and made VTE terminals render fully transparent.
#   feh        sets the wallpaper (generated at build time, see below)
#   rofi       app launcher (Alt+d)
#   sakura     lightweight VTE terminal (Alt+Return)
#   x11vnc     VNC server exporting :99 on TCP 5900 (the sandbox service
#              relays the browser's WebSocket to this)
#   x11-utils  xdpyinfo — used to wait for the display to come up
#
# Keybindings use Alt as the modifier — the Super/Win key never reaches the
# sandbox because the host OS and browser capture it first.
#
# Built via the `sandbox-desktop-image` compose service; sandbox-service
# waits for that build to finish before it starts.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb \
      x11-utils \
      x11-xserver-utils \
      x11vnc \
      xkb-data \
      xdotool \
      openbox \
      tint2 \
      conky-all \
      feh \
      rofi \
      sakura \
      fastfetch \
      fonts-jetbrains-mono \
      fonts-dejavu-core \
      procps \
      ca-certificates \
      curl \
      git \
    && rm -rf /var/lib/apt/lists/*

# xkb-data: XKEYBOARD keymaps, required for x11vnc's `-xkb` (full keyboard).
# xdotool: lets the agent (and debugging) drive the X session synthetically.

# --- Wallpaper: generated at build time (no licensing, no downloads) ---------
# Firewatch-style dusk scene — gradient sky, stars, layered mountain ridges,
# pine silhouettes and a fire-lookout tower. Deterministic (seeded).
RUN pip install --no-cache-dir pillow

COPY <<'EOF' /opt/praxis/wallpaper.py
"""Generate the sandbox wallpaper: dusk sky, stars, mountain layers, pines,
and a fire-lookout tower. Deterministic output (fixed seed)."""
import random

from PIL import Image, ImageDraw

random.seed(7)
W, H = 1920, 1080
img = Image.new("RGB", (W, H))
d = ImageDraw.Draw(img)

# Dusk gradient: deep night blue -> soft slate at the horizon.
top, bottom = (18, 20, 38), (96, 114, 154)
for y in range(H):
    t = (y / H) ** 1.3
    d.line(
        [(0, y), (W, y)],
        fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)),
    )

# Stars, denser near the top.
for _ in range(260):
    x = random.randrange(W)
    y = int(random.triangular(0, H * 0.55, 0))
    b = random.randint(110, 230)
    d.point((x, y), fill=(b, b, min(255, b + 25)))
    if b > 200:
        d.point((x + 1, y), fill=(b - 60, b - 60, b - 40))

def ridge(base_frac, rough, color):
    """One mountain silhouette layer."""
    pts = [(0, H)]
    y = H * base_frac
    x = 0
    while x <= W:
        pts.append((x, y))
        x += random.randint(36, 84)
        lo, hi = H * base_frac - rough, H * base_frac + rough * 0.6
        y = min(hi, max(lo, y + random.randint(-rough, rough)))
    pts += [(W, H)]
    d.polygon(pts, fill=color)
    return pts

ridge(0.48, 70, (58, 72, 112))
tower_ridge = ridge(0.60, 80, (44, 55, 92))
ridge(0.74, 90, (32, 40, 70))
front = ridge(0.88, 60, (20, 25, 46))

# Fire-lookout tower on the second ridge. Legs run well past the ridge crest
# (max height base*H - rough) so the tower always reads as planted on it.
tx, ty = int(W * 0.62), int(H * 0.545)
tower = (30, 38, 66)
d.rectangle([tx - 26, ty - 34, tx + 26, ty - 6], fill=tower)          # cabin
d.rectangle([tx - 34, ty - 40, tx + 34, ty - 34], fill=tower)         # roof
d.line([tx - 20, ty - 6, tx - 32, ty + 120], fill=tower, width=5)     # legs
d.line([tx + 20, ty - 6, tx + 32, ty + 120], fill=tower, width=5)
d.line([tx - 26, ty + 20, tx + 26, ty + 40], fill=tower, width=3)     # braces
d.line([tx + 26, ty + 20, tx - 26, ty + 40], fill=tower, width=3)
d.rectangle([tx - 14, ty - 28, tx + 14, ty - 14], fill=(140, 150, 120))  # lit window

# Pine silhouettes along the front ridge.
pine = (12, 15, 30)
for _ in range(90):
    x = random.randrange(W)
    y = int(H * 0.88) + random.randint(-18, 40)
    h = random.randint(26, 64)
    w = h // 3
    d.polygon([(x, y - h), (x - w, y), (x + w, y)], fill=pine)
    d.polygon([(x, y - h - h // 3), (x - w * 2 // 3, y - h // 2), (x + w * 2 // 3, y - h // 2)], fill=pine)

img.save("/usr/share/backgrounds/praxis.png")
EOF

RUN mkdir -p /usr/share/backgrounds && python /opt/praxis/wallpaper.py

# --- Openbox: dark theme + Alt keybindings (patch the stock rc.xml) ----------
# The stock rc.xml keeps all default mouse behaviour (move/resize/focus);
# we only switch the theme and inject a few keybinds before </keyboard>.
RUN sed -i 's#<name>Clearlooks</name>#<name>Onyx</name>#' /etc/xdg/openbox/rc.xml \
    && sed -i 's#</keyboard>#\
  <keybind key="A-Return"><action name="Execute"><command>sakura</command></action></keybind>\
  <keybind key="A-d"><action name="Execute"><command>rofi -show drun</command></action></keybind>\
  <keybind key="A-q"><action name="Close"/></keybind>\
</keyboard>#' /etc/xdg/openbox/rc.xml

# Autostart for `openbox-session`: wallpaper, compositor, panel, widgets, and
# a terminal so the desktop is immediately usable.
COPY <<'EOF' /etc/xdg/openbox/autostart
feh --bg-fill /usr/share/backgrounds/praxis.png &
tint2 &
# conky pseudo-transparency reads the root pixmap feh just set — start after.
(sleep 0.4; conky) &
# cd first: the VTE child shell inherits sakura's cwd — terminals should
# open in the user's workspace, not $HOME.
(sleep 0.6; cd /workspace && sakura) &
EOF

# --- tint2: slim translucent dark top bar ------------------------------------
COPY <<'EOF' /etc/xdg/tint2/tint2rc
# Background 1: panel
rounded = 0
border_width = 0
background_color = #14151f 80

# Background 2: active task
rounded = 6
border_width = 0
background_color = #7aa2f7 25

panel_items = TC
panel_position = top center horizontal
panel_size = 100% 32
panel_padding = 8 4 8
panel_background_id = 1
panel_layer = top
wm_menu = 1

taskbar_mode = single_desktop
taskbar_padding = 2 0 4
taskbar_name = 0

task_text = 1
task_maximum_size = 180 28
task_padding = 8 3 8
task_font = JetBrains Mono 9
task_font_color = #a9b1d6 100
task_active_font_color = #c0caf5 100
task_active_background_id = 2

time1_format = %H:%M
time1_font = JetBrains Mono Bold 10
clock_font_color = #c0caf5 100
clock_padding = 8 0
EOF

# --- conky: right-side system widgets ----------------------------------------
COPY <<'EOF' /etc/conky/conky.conf
conky.config = {
    alignment = 'top_right',
    gap_x = 28,
    gap_y = 56,
    minimum_width = 230,
    maximum_width = 230,
    own_window = true,
    own_window_type = 'desktop',
    -- Pseudo-transparency: paints the wallpaper (root pixmap) as its own
    -- background. Works without a compositor.
    own_window_transparent = true,
    own_window_argb_visual = false,
    double_buffer = true,
    use_xft = true,
    font = 'JetBrains Mono:size=10',
    default_color = 'c0caf5',
    color1 = '7aa2f7',
    draw_shades = false,
    update_interval = 2,
    border_inner_margin = 14,
}

conky.text = [[
${font JetBrains Mono:bold:size=30}${color1}${time %H:%M}${color}${font}
${time %A, %d %B}
${color1}${hr 1}${color}
cpu   ${cpu}%  ${cpubar 5}
mem   ${memperc}%  ${membar 5}
disk  ${fs_used_perc /}%  ${fs_bar 5 /}
${color1}${hr 1}${color}
${font JetBrains Mono:size=9}Alt+Enter terminal  ·  Alt+d apps${font}
]]
EOF

# --- terminal + launcher theming (root is the sandbox user) ------------------
COPY <<'EOF' /root/.config/sakura/sakura.conf
[sakura]
font=JetBrains Mono 11
colorset1_fore=rgb(192,202,245)
colorset1_back=rgb(20,21,31)
scrollbar=false
closebutton=false
less_questions=true
EOF

COPY <<'EOF' /root/.config/rofi/config.rasi
configuration {
  show-icons: false;
}
@theme "Arc-Dark"
EOF

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
# display the client starts on :99. SHELL must be set explicitly: docker
# exec doesn't provide it, and VTE terminals (sakura) refuse to spawn a
# child without it (VTE-CRITICAL: assertion 'argv[0] != nullptr') — the
# terminal then opens shell-less and silently swallows all keystrokes.
ENV DISPLAY=:99 \
    SHELL=/bin/bash

# CMD is irrelevant: sandbox-service overrides it with `sleep infinity` to
# keep the container alive, then boots the X + VNC stack lazily.
