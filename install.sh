#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Surround Mixer — Installer
# Makes the app double-clickable from your application menu.
# ──────────────────────────────────────────────────────────────

set -e

APP_NAME="surround-mixer"
INSTALL_DIR="$HOME/.local/share/surround-mixer"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

echo "┌─────────────────────────────────────────┐"
echo "│  SURROUND MIXER — Installer             │"
echo "│  The volume knob that should have existed│"
echo "└─────────────────────────────────────────┘"
echo ""

# ── Check dependencies ──
echo "Checking dependencies..."

MISSING=""

if ! command -v python3 &>/dev/null; then
    MISSING="$MISSING python3"
fi

if ! python3 -c "import gi; gi.require_version('Gtk', '4.0')" &>/dev/null 2>&1; then
    MISSING="$MISSING python3-gi gir1.2-gtk-4.0"
fi

if ! command -v pw-cli &>/dev/null; then
    MISSING="$MISSING pipewire"
fi

if [ -n "$MISSING" ]; then
    echo "Missing packages:$MISSING"
    echo ""
    echo "Install them with:"
    echo "  sudo apt install$MISSING"
    echo ""
    read -p "Install now? [Y/n] " yn
    case $yn in
        [Nn]* )
            echo "Aborting. Install dependencies and re-run."
            exit 1
            ;;
        * )
            sudo apt install -y $MISSING
            ;;
    esac
fi

echo "  ✓ Dependencies OK"

# ── Create directories ──
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$ICON_DIR"

# ── Copy the app ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/surround_mixer.py" ]; then
    cp "$SCRIPT_DIR/surround_mixer.py" "$INSTALL_DIR/surround_mixer.py"
    echo "  ✓ App installed to $INSTALL_DIR"
else
    echo "  ✗ surround_mixer.py not found in $SCRIPT_DIR"
    echo "    Put this script in the same folder as surround_mixer.py"
    exit 1
fi

chmod +x "$INSTALL_DIR/surround_mixer.py"

# ── Create launcher script ──
cat > "$BIN_DIR/surround-mixer" << 'EOF'
#!/bin/bash
exec python3 "$HOME/.local/share/surround-mixer/surround_mixer.py" "$@"
EOF
chmod +x "$BIN_DIR/surround-mixer"
echo "  ✓ Launcher created at $BIN_DIR/surround-mixer"

# ── Create SVG icon ──
cat > "$ICON_DIR/surround-mixer.svg" << 'SVGEOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1a1a1a"/>
      <stop offset="100%" stop-color="#0a0a0a"/>
    </linearGradient>
  </defs>
  <!-- Background -->
  <rect width="256" height="256" rx="48" fill="url(#bg)"/>
  <!-- Fader tracks -->
  <rect x="56" y="60" width="8" height="136" rx="4" fill="#2a2a2a"/>
  <rect x="96" y="60" width="8" height="136" rx="4" fill="#2a2a2a"/>
  <rect x="152" y="60" width="8" height="136" rx="4" fill="#2a2a2a"/>
  <rect x="192" y="60" width="8" height="136" rx="4" fill="#2a2a2a"/>
  <!-- Fader fills -->
  <rect x="56" y="120" width="8" height="76" rx="4" fill="#4ECDC4" opacity="0.6"/>
  <rect x="96" y="80" width="8" height="116" rx="4" fill="#FFE66D" opacity="0.8"/>
  <rect x="152" y="80" width="8" height="116" rx="4" fill="#FFE66D" opacity="0.8"/>
  <rect x="192" y="140" width="8" height="56" rx="4" fill="#7B68EE" opacity="0.6"/>
  <!-- Fader knobs -->
  <rect x="48" y="114" width="24" height="10" rx="4" fill="#ffffff"/>
  <rect x="88" y="74" width="24" height="10" rx="4" fill="#ffffff"/>
  <rect x="144" y="74" width="24" height="10" rx="4" fill="#ffffff"/>
  <rect x="184" y="134" width="24" height="10" rx="4" fill="#ffffff"/>
  <!-- Center channel highlight -->
  <rect x="86" y="56" width="28" height="4" rx="2" fill="#FFE66D" opacity="0.4"/>
  <rect x="142" y="56" width="28" height="4" rx="2" fill="#FFE66D" opacity="0.4"/>
  <!-- Label: C -->
  <text x="128" y="228" text-anchor="middle" font-family="monospace" font-weight="900"
        font-size="28" fill="#FFE66D" opacity="0.8">C</text>
</svg>
SVGEOF
echo "  ✓ Icon installed"

# ── Create .desktop file ──
cat > "$DESKTOP_DIR/surround-mixer.desktop" << EOF
[Desktop Entry]
Name=Surround Mixer
Comment=PipeWire 5.1 to Stereo downmix with per-channel gain control
Exec=$BIN_DIR/surround-mixer
Icon=surround-mixer
Terminal=false
Type=Application
Categories=Audio;AudioVideo;Mixer;Settings;
Keywords=audio;mixer;surround;pipewire;center;channel;dialog;
StartupNotify=true
EOF
echo "  ✓ Desktop entry created"

# ── Update desktop database ──
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi
echo "  ✓ Desktop database updated"

# ── Ensure ~/.local/bin is in PATH ──
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  ⚠ $BIN_DIR is not in your PATH."
    echo "    Add this to your ~/.bashrc or ~/.profile:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ── Install PipeWire config if not present ──
CONFIG_DIR="$HOME/.config/pipewire/pipewire.conf.d"
CONFIG_FILE="$CONFIG_DIR/center-boost.conf"

if [ ! -f "$CONFIG_FILE" ]; then
    echo ""
    read -p "Install PipeWire mixer config now? [Y/n] " yn
    case $yn in
        [Nn]* )
            echo "  Skipped. You can install it later from inside the app."
            ;;
        * )
            mkdir -p "$CONFIG_DIR"
            python3 -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
from surround_mixer import generate_config, CHANNELS
gains = {ch['id']: ch['default'] for ch in CHANNELS}
print(generate_config(gains))
" > "$CONFIG_FILE"
            echo "  ✓ PipeWire config installed"
            echo "  Restarting PipeWire..."
            systemctl --user restart pipewire pipewire-pulse 2>/dev/null || true
            echo "  ✓ PipeWire restarted"
            ;;
    esac
fi

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│  ✓ Installation complete!               │"
echo "│                                         │"
echo "│  Launch from your app menu or run:      │"
echo "│    surround-mixer                       │"
echo "│                                         │"
echo "│  To uninstall:                          │"
echo "│    rm -rf $INSTALL_DIR"
echo "│    rm $BIN_DIR/surround-mixer"
echo "│    rm $DESKTOP_DIR/surround-mixer.desktop"
echo "│    rm $ICON_DIR/surround-mixer.svg"
echo "└─────────────────────────────────────────┘"
