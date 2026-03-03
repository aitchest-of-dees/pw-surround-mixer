#!/usr/bin/env python3
"""
Surround Mixer v3 — The Volume Knob That Should Have Existed
A GTK4 app for real-time control of PipeWire 5.1→Stereo downmix gains.

Features:
  - Real-time gain control per channel via pw-cli (no restart needed)
  - L/R channel locking (front and rear independently)
  - File-based presets (~/.config/surround-mixer/presets/*.json)
  - PipeWire health monitoring with setup guidance
  - Auto-writes PipeWire config so settings survive reboots

Requirements:
  sudo apt install python3-gi gir1.2-gtk-4.0 pipewire

Usage:
  python3 surround_mixer.py
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
import subprocess
import json
import math
import re
import os
import sys
import glob

# ─── Paths ───────────────────────────────────────────────────────────
APP_DIR = os.path.expanduser("~/.config/surround-mixer")
PRESETS_DIR = os.path.join(APP_DIR, "presets")
STATE_FILE = os.path.join(APP_DIR, "state.json")
PW_CONFIG_DIR = os.path.expanduser("~/.config/pipewire/pipewire.conf.d")
PW_CONFIG_FILE = os.path.join(PW_CONFIG_DIR, "center-boost.conf")

# ─── Channel Definitions ────────────────────────────────────────────
CHANNELS = [
    {"id": "fl",  "label": "FL",  "name": "Front Left",      "color": "#4ECDC4", "default": 1.0,  "group": "front"},
    {"id": "fc",  "label": "C",   "name": "Center",          "color": "#FFE66D", "default": 1.2,  "group": None},
    {"id": "fr",  "label": "FR",  "name": "Front Right",     "color": "#4ECDC4", "default": 1.0,  "group": "front"},
    {"id": "sl",  "label": "SL",  "name": "Surround Left",   "color": "#7B68EE", "default": 0.7,  "group": "rear"},
    {"id": "lfe", "label": "LFE", "name": "Subwoofer",       "color": "#FF6B6B", "default": 0.5,  "group": None},
    {"id": "sr",  "label": "SR",  "name": "Surround Right",  "color": "#7B68EE", "default": 0.7,  "group": "rear"},
]

CHANNEL_BY_ID = {ch["id"]: ch for ch in CHANNELS}
DEFAULT_GAINS = {ch["id"]: ch["default"] for ch in CHANNELS}

LOCK_PAIRS = {
    "front": ("fl", "fr"),
    "rear":  ("sl", "sr"),
}

# mixL: In 1=FL, In 2=FC, In 3=SL, In 4=LFE
# mixR: In 1=FR, In 2=FC, In 3=SR, In 4=LFE
GAIN_TO_CONTROLS = {
    "fl":  [("mixL", "Gain 1")],
    "fr":  [("mixR", "Gain 1")],
    "fc":  [("mixL", "Gain 2"), ("mixR", "Gain 2")],
    "sl":  [("mixL", "Gain 3")],
    "sr":  [("mixR", "Gain 3")],
    "lfe": [("mixL", "Gain 4"), ("mixR", "Gain 4")],
}

DEFAULT_PRESETS = {
    "Dialog Boost": {"fl": 0.8,  "fc": 1.5, "fr": 0.8,  "sl": 0.4, "lfe": 0.5, "sr": 0.5},
    "Standard":     {"fl": 1.0,  "fc": 1.0, "fr": 1.0,  "sl": 0.7, "lfe": 0.5, "sr": 0.6},
    "Late Night":   {"fl": 0.5,  "fc": 1.5, "fr": 0.5,  "sl": 0.2, "lfe": 0.2, "sr": 0.2},
    "Center Heavy": {"fl": 0.6,  "fc": 1.8, "fr": 0.6,  "sl": 0.3, "lfe": 0.4, "sr": 0.4},
}


# ─── Preset File I/O ────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(PRESETS_DIR, exist_ok=True)
    os.makedirs(PW_CONFIG_DIR, exist_ok=True)


def init_default_presets():
    """Copy default presets into the presets dir on first run."""
    ensure_dirs()
    for name, gains in DEFAULT_PRESETS.items():
        path = os.path.join(PRESETS_DIR, f"{name}.json")
        if not os.path.exists(path):
            with open(path, 'w') as f:
                json.dump(gains, f, indent=2)


def load_all_presets():
    """Read all preset files from disk. Returns dict of {name: gains}."""
    presets = {}
    for path in sorted(glob.glob(os.path.join(PRESETS_DIR, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            # Validate: must have all channel keys
            gains = {}
            for ch in CHANNELS:
                gains[ch["id"]] = float(data.get(ch["id"], ch["default"]))
            presets[name] = gains
        except Exception as e:
            print(f"Bad preset {path}: {e}", file=sys.stderr)
    return presets


def save_preset(name, gains):
    """Write a preset file."""
    ensure_dirs()
    path = os.path.join(PRESETS_DIR, f"{name}.json")
    with open(path, 'w') as f:
        json.dump(gains, f, indent=2)


def delete_preset(name):
    """Delete a preset file."""
    path = os.path.join(PRESETS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)


def load_state():
    """Load app state (active preset name, locks)."""
    defaults = {"active": None, "locks": {"front": True, "rear": True}}
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
        return {
            "active": data.get("active"),
            "locks": data.get("locks", defaults["locks"]),
            "gains": data.get("gains", DEFAULT_GAINS.copy()),
        }
    except Exception:
        return {**defaults, "gains": DEFAULT_GAINS.copy()}


def save_state(active_name, locks, gains):
    """Save app state."""
    ensure_dirs()
    with open(STATE_FILE, 'w') as f:
        json.dump({"active": active_name, "locks": locks, "gains": gains}, f, indent=2)


# ─── PipeWire ────────────────────────────────────────────────────────

def generate_config(gains):
    g = gains
    return f"""# PipeWire 5.1→Stereo Downmix — Surround Mixer
# Auto-generated. Edit presets in ~/.config/surround-mixer/presets/

context.modules = [
  {{
    name = libpipewire-module-filter-chain
    args = {{
      node.description = "Surround Mixer"
      media.name       = "Surround Mixer"
      filter.graph = {{
        nodes = [
          {{ type = builtin  name = copyFL   label = copy }}
          {{ type = builtin  name = copyFR   label = copy }}
          {{ type = builtin  name = copyFC   label = copy }}
          {{ type = builtin  name = copySL   label = copy }}
          {{ type = builtin  name = copySR   label = copy }}
          {{ type = builtin  name = copyLFE  label = copy }}
          {{
            type = builtin
            name = mixL
            label = mixer
            control = {{
              "Gain 1" = {g['fl']:.4f}
              "Gain 2" = {g['fc']:.4f}
              "Gain 3" = {g['sl']:.4f}
              "Gain 4" = {g['lfe']:.4f}
            }}
          }}
          {{
            type = builtin
            name = mixR
            label = mixer
            control = {{
              "Gain 1" = {g['fr']:.4f}
              "Gain 2" = {g['fc']:.4f}
              "Gain 3" = {g['sr']:.4f}
              "Gain 4" = {g['lfe']:.4f}
            }}
          }}
        ]
        links = [
          {{ output = "copyFL:Out"   input = "mixL:In 1" }}
          {{ output = "copyFC:Out"   input = "mixL:In 2" }}
          {{ output = "copySL:Out"   input = "mixL:In 3" }}
          {{ output = "copyLFE:Out"  input = "mixL:In 4" }}
          {{ output = "copyFR:Out"   input = "mixR:In 1" }}
          {{ output = "copyFC:Out"   input = "mixR:In 2" }}
          {{ output = "copySR:Out"   input = "mixR:In 3" }}
          {{ output = "copyLFE:Out"  input = "mixR:In 4" }}
        ]
        inputs  = [ "copyFL:In" "copyFR:In" "copyFC:In" "copyLFE:In" "copySL:In" "copySR:In" ]
        outputs = [ "mixL:Out" "mixR:Out" ]
      }}
      capture.props = {{
        node.name   = "effect_input.surround_mixer"
        media.class = Audio/Sink
        audio.channels = 6
        audio.position = [ FL FR FC LFE SL SR ]
      }}
      playback.props = {{
        node.name    = "effect_output.surround_mixer"
        node.passive = true
        audio.channels = 2
        audio.position = [ FL FR ]
      }}
    }}
  }}
]
"""


class PipeWireStatus:
    def __init__(self):
        self.pipewire_running = False
        self.pipewire_pulse_running = False
        self.is_pipewire_server = False
        self.filter_node_id = None
        self.config_file_exists = False

    @property
    def all_good(self):
        return (self.pipewire_running and self.is_pipewire_server and
                self.filter_node_id and self.config_file_exists)

    @property
    def summary_markup(self):
        if self.all_good:
            return (f'<span color="#4ECDC4" font_family="monospace" font_size="small">'
                    f'● Connected · node {self.filter_node_id}</span>')
        parts = []
        if not self.pipewire_running:
            parts.append('<span color="#FF6B6B">✗ PipeWire not running</span>')
        elif not self.is_pipewire_server:
            parts.append('<span color="#FF6B6B">✗ PipeWire not responding</span>')
        if not self.config_file_exists:
            parts.append('<span color="#FFE66D">⚠ Config not installed</span>')
        elif not self.filter_node_id:
            parts.append('<span color="#FF6B6B">✗ Mixer sink not loaded</span>')
        return ('<span font_family="monospace" font_size="small">' +
                ' · '.join(parts) + '</span>')

    @property
    def action_hint(self):
        if not self.pipewire_running:
            return ("PipeWire is not running. Install it:\n"
                    "  sudo apt install pipewire pipewire-pulse wireplumber\n"
                    "  systemctl --user --now disable pulseaudio.service pulseaudio.socket\n"
                    "  systemctl --user --now enable pipewire pipewire-pulse wireplumber\n"
                    "  Then log out and back in.")
        if not self.is_pipewire_server:
            return ("PipeWire is not responding. Try:\n"
                    "  systemctl --user restart pipewire pipewire-pulse")
        if not self.config_file_exists or not self.filter_node_id:
            return "Click INSTALL & RESTART PIPEWIRE below to set up the mixer."
        return None


def check_pipewire_status():
    status = PipeWireStatus()
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "pipewire"],
                           capture_output=True, text=True, timeout=3)
        status.pipewire_running = r.stdout.strip() == "active"
    except Exception:
        pass

    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "pipewire-pulse"],
                           capture_output=True, text=True, timeout=3)
        status.pipewire_pulse_running = r.stdout.strip() == "active"
    except Exception:
        pass

    if status.pipewire_running:
        try:
            r = subprocess.run(["pw-cli", "info", "0"],
                               capture_output=True, text=True, timeout=3)
            status.is_pipewire_server = r.returncode == 0
        except Exception:
            pass

    status.config_file_exists = os.path.isfile(PW_CONFIG_FILE)

    if status.pipewire_running:
        status.filter_node_id = find_filter_chain_node_id()

    return status


def find_filter_chain_node_id():
    try:
        result = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        for obj in data:
            props = obj.get("info", {}).get("props", {})
            if props.get("node.description") == "Surround Mixer":
                return str(obj["id"])
            if props.get("media.name") == "Surround Mixer":
                return str(obj["id"])
    except Exception:
        pass
    return None


def set_gain_runtime(node_id, mixer_name, control_name, value):
    param_key = f"{mixer_name}:{control_name}"
    cmd = [
        "pw-cli", "set-param", node_id, "Props",
        f'{{params = ["{param_key}" {value:.6f}]}}'
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        return True
    except Exception as e:
        print(f"pw-cli error: {e}", file=sys.stderr)
        return False


def write_pw_config(gains):
    """Silently write the PipeWire config so settings survive reboots."""
    try:
        ensure_dirs()
        with open(PW_CONFIG_FILE, 'w') as f:
            f.write(generate_config(gains))
    except Exception as e:
        print(f"Config write error: {e}", file=sys.stderr)


def gain_to_db(gain):
    if gain < 0.001:
        return "-∞ dB"
    db = 20 * math.log10(gain)
    sign = "+" if db >= 0 else ""
    return f"{sign}{db:.1f} dB"


# ─── CSS ─────────────────────────────────────────────────────────────
CSS = b"""
window { background-color: #0a0a0a; }
.preset-button {
    background: rgba(255,255,255,0.04); border: 1px solid #222;
    color: #888; font-size: 11px; padding: 5px 12px;
    border-radius: 6px; min-height: 0; min-width: 0;
}
.preset-button:hover { background: rgba(255,255,255,0.08); color: #ccc; }
.preset-button.active {
    background: rgba(255,230,109,0.15); border-color: rgba(255,230,109,0.3);
    color: #FFE66D; font-weight: 700;
}
.save-preset-button {
    background: rgba(255,230,109,0.08); border: 1px solid rgba(255,230,109,0.2);
    color: #FFE66D; font-size: 11px; font-weight: 700; padding: 5px 12px;
    border-radius: 6px; min-height: 0; min-width: 0;
}
.save-preset-button:hover { background: rgba(255,230,109,0.15); }
.install-button {
    background: rgba(78,205,196,0.08); border: 1px solid rgba(78,205,196,0.2);
    color: #4ECDC4; font-size: 11px; font-weight: 700; padding: 8px 16px;
    border-radius: 8px; min-height: 0;
}
.install-button:hover { background: rgba(78,205,196,0.15); }
.hint-box {
    background: rgba(255,107,107,0.06); border: 1px solid rgba(255,107,107,0.15);
    border-radius: 8px; padding: 12px;
}
scale trough {
    background: #1a1a1a; border-radius: 6px;
    min-width: 10px; border: 1px solid #2a2a2a;
}
scale trough highlight { border-radius: 6px; min-width: 10px; }
scale slider {
    background: #fff; min-width: 26px; min-height: 14px;
    border-radius: 4px; box-shadow: 0 0 6px rgba(255,255,255,0.3);
    margin: 0; padding: 0;
}
.save-entry {
    background: #111; border: 1px solid #333; color: #eee;
    border-radius: 6px; padding: 6px 10px; font-size: 12px;
    min-height: 0;
}
.save-entry:focus { border-color: #FFE66D; }
"""


# ─── Widgets ─────────────────────────────────────────────────────────

class ChannelStrip(Gtk.Box):
    def __init__(self, channel_def, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_halign(Gtk.Align.CENTER)
        self.set_size_request(85, -1)
        self.channel = channel_def
        self.on_change = on_change
        self._suppress = False
        color = channel_def["color"]

        name_label = Gtk.Label()
        name_label.set_markup(
            f'<span color="{color}" font_size="x-small" '
            f'letter_spacing="1000" font_weight="bold">'
            f'{channel_def["name"].upper()}</span>')
        self.append(name_label)

        self.db_label = Gtk.Label()
        self.append(self.db_label)

        adj = Gtk.Adjustment(value=channel_def["default"], lower=0.0, upper=2.5,
                             step_increment=0.01, page_increment=0.1)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj)
        self.scale.set_inverted(True)
        self.scale.set_draw_value(False)
        self.scale.set_vexpand(True)
        self.scale.set_size_request(40, 180)
        self.scale.add_mark(1.0, Gtk.PositionType.RIGHT, None)
        self.scale.connect("value-changed", self._on_changed)
        self.append(self.scale)

        self.gain_label = Gtk.Label()
        self.append(self.gain_label)

        big = Gtk.Label()
        big.set_markup(
            f'<span color="{color}" font_weight="ultrabold" font_size="large">'
            f'{channel_def["label"]}</span>')
        self.append(big)

        self._update(channel_def["default"])

    def _on_changed(self, scale):
        if self._suppress:
            return
        self._update(scale.get_value())
        self.on_change(self.channel["id"], scale.get_value())

    def _update(self, val):
        color = "#444" if val < 0.01 else ("#FF6B6B" if val > 1.2 else "#e0e0e0")
        self.db_label.set_markup(
            f'<span color="{color}" font_family="monospace" '
            f'font_size="medium" font_weight="bold">{gain_to_db(val)}</span>')
        self.gain_label.set_markup(
            f'<span color="#666" font_family="monospace" font_size="x-small">×{val:.2f}</span>')

    def set_value(self, val):
        self._suppress = True
        self.scale.set_value(val)
        self._update(val)
        self._suppress = False

    def get_value(self):
        return self.scale.get_value()


# ─── Main App ────────────────────────────────────────────────────────

class SurroundMixerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.neckbeard.surround-mixer")

        init_default_presets()
        state = load_state()
        self.gains = state["gains"]
        self.locks = state["locks"]
        self.active_preset = state["active"]

        self.presets = load_all_presets()
        self.node_id = None
        self.strips = {}
        self._pending = {}
        self._timer = None
        self._health_timer = None
        self._config_timer = None

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.ApplicationWindow(application=self)
        self.win.set_title("Surround Mixer")
        self.win.set_default_size(640, 600)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main.set_margin_top(16)
        main.set_margin_bottom(16)
        main.set_margin_start(20)
        main.set_margin_end(20)

        # Header
        title = Gtk.Label()
        title.set_markup(
            '<span font_weight="ultrabold" font_size="x-large" color="#fff">'
            'SURROUND MIXER</span>')
        title.set_halign(Gtk.Align.START)
        main.append(title)

        sub = Gtk.Label()
        sub.set_markup(
            '<span color="#555" font_size="x-small" letter_spacing="2000">'
            'PIPEWIRE 5.1→STEREO · REAL-TIME</span>')
        sub.set_halign(Gtk.Align.START)
        main.append(sub)

        # Status
        self.status_label = Gtk.Label()
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_wrap(True)
        main.append(self.status_label)

        # Hint box
        self.hint_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hint_box.add_css_class("hint-box")
        self.hint_label = Gtk.Label()
        self.hint_label.set_halign(Gtk.Align.START)
        self.hint_label.set_wrap(True)
        self.hint_label.set_selectable(True)
        self.hint_box.append(self.hint_label)
        self.hint_box.set_visible(False)
        main.append(self.hint_box)

        # ── Presets area ──
        self.presets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Preset buttons row
        self.preset_flow = Gtk.FlowBox()
        self.preset_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.preset_flow.set_max_children_per_line(8)
        self.preset_flow.set_column_spacing(4)
        self.preset_flow.set_row_spacing(4)
        self.presets_box.append(self.preset_flow)

        # Save preset row
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save_row.set_halign(Gtk.Align.START)

        self.save_entry = Gtk.Entry()
        self.save_entry.set_placeholder_text("Preset name...")
        self.save_entry.set_max_length(40)
        self.save_entry.set_size_request(180, -1)
        self.save_entry.add_css_class("save-entry")
        self.save_entry.connect("activate", self._on_save_preset)
        save_row.append(self.save_entry)

        save_btn = Gtk.Button(label="SAVE PRESET")
        save_btn.add_css_class("save-preset-button")
        save_btn.connect("clicked", self._on_save_preset)
        save_row.append(save_btn)

        self.presets_box.append(save_row)
        main.append(self.presets_box)

        # ── Front row with lock ──
        front_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        front_hbox.set_halign(Gtk.Align.CENTER)

        front_lock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        front_lock_box.set_valign(Gtk.Align.CENTER)
        front_lock_box.set_margin_end(6)
        self.front_lock = Gtk.CheckButton()
        self.front_lock.set_active(self.locks.get("front", True))
        self.front_lock.connect("toggled", self._on_lock, "front")
        front_lock_box.append(self.front_lock)
        fl_lbl = Gtk.Label()
        fl_lbl.set_markup('<span color="#666" font_size="xx-small">LOCK\nL/R</span>')
        fl_lbl.set_justify(Gtk.Justification.CENTER)
        front_lock_box.append(fl_lbl)
        front_hbox.append(front_lock_box)

        for ch in CHANNELS[:3]:
            strip = ChannelStrip(ch, self._on_gain)
            self.strips[ch["id"]] = strip
            front_hbox.append(strip)
        main.append(front_hbox)

        # ── Rear row with lock ──
        rear_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        rear_hbox.set_halign(Gtk.Align.CENTER)

        rear_lock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rear_lock_box.set_valign(Gtk.Align.CENTER)
        rear_lock_box.set_margin_end(6)
        self.rear_lock = Gtk.CheckButton()
        self.rear_lock.set_active(self.locks.get("rear", True))
        self.rear_lock.connect("toggled", self._on_lock, "rear")
        rear_lock_box.append(self.rear_lock)
        rl_lbl = Gtk.Label()
        rl_lbl.set_markup('<span color="#666" font_size="xx-small">LOCK\nL/R</span>')
        rl_lbl.set_justify(Gtk.Justification.CENTER)
        rear_lock_box.append(rl_lbl)
        rear_hbox.append(rear_lock_box)

        for ch in CHANNELS[3:]:
            strip = ChannelStrip(ch, self._on_gain)
            self.strips[ch["id"]] = strip
            rear_hbox.append(strip)
        main.append(rear_hbox)

        # ── Install button (for first-time setup) ──
        self.install_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.install_box.set_halign(Gtk.Align.CENTER)
        self.install_box.set_margin_top(6)

        install_btn = Gtk.Button(label="INSTALL & RESTART PIPEWIRE")
        install_btn.add_css_class("install-button")
        install_btn.connect("clicked", self._on_install)
        self.install_box.append(install_btn)
        main.append(self.install_box)

        # Footer
        footer = Gtk.Label()
        footer.set_markup(
            '<span color="#333" font_size="xx-small" letter_spacing="2000">'
            'BECAUSE THERE SHOULD BE A FUCKING VOLUME KNOB</span>')
        footer.set_margin_top(10)
        main.append(footer)

        scroll.set_child(main)
        self.win.set_child(scroll)

        # Build preset buttons
        self._rebuild_preset_buttons()

        # Apply saved gains
        for ch_id, val in self.gains.items():
            if ch_id in self.strips:
                self.strips[ch_id].set_value(val)

        # Health check
        self._do_health_check()
        self._health_timer = GLib.timeout_add_seconds(10, self._do_health_check)

        self.win.present()

    # ── Presets UI ──

    def _rebuild_preset_buttons(self):
        """Rebuild preset buttons from disk."""
        # Clear existing
        child = self.preset_flow.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.preset_flow.remove(child)
            child = next_child

        self.preset_buttons = {}
        self.presets = load_all_presets()

        for name in self.presets:
            btn = Gtk.Button(label=name)
            btn.add_css_class("preset-button")
            if name == self.active_preset:
                btn.add_css_class("active")
            btn.connect("clicked", self._on_preset, name)
            self.preset_flow.insert(btn, -1)
            self.preset_buttons[name] = btn

    def _on_preset(self, button, name):
        if name not in self.presets:
            return
        preset = self.presets[name]
        self.gains.update(preset)
        self.active_preset = name

        for ch_id, val in preset.items():
            if ch_id in self.strips:
                self.strips[ch_id].set_value(val)

        for pname, btn in self.preset_buttons.items():
            if pname == name:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

        # Populate save entry with current preset name
        self.save_entry.set_text(name)

        # Push all to PipeWire
        for ch_id, val in preset.items():
            for mixer, ctrl in GAIN_TO_CONTROLS.get(ch_id, []):
                self._pending[(mixer, ctrl)] = val
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(50, self._flush)

    def _on_save_preset(self, widget):
        name = self.save_entry.get_text().strip()
        if not name:
            return

        # Sanitize filename
        safe_name = re.sub(r'[^\w\s\-]', '', name).strip()
        if not safe_name:
            return

        # Read gains directly from the sliders — source of truth
        gains = {}
        for ch in CHANNELS:
            if ch["id"] in self.strips:
                gains[ch["id"]] = round(self.strips[ch["id"]].get_value(), 4)
        
        self.gains = gains
        save_preset(safe_name, gains)
        self.active_preset = safe_name
        self._rebuild_preset_buttons()
        self._save_all()

        self.status_label.set_markup(
            f'<span color="#4ECDC4" font_family="monospace" font_size="small">'
            f'● Preset saved: {safe_name}</span>')

    # ── Gains ──

    def _on_gain(self, channel_id, value):
        value = round(value, 4)
        self.gains[channel_id] = value
        self.active_preset = None
        for btn in self.preset_buttons.values():
            btn.remove_css_class("active")

        ch = CHANNEL_BY_ID[channel_id]
        if ch["group"] and self.locks.get(ch["group"], False):
            left_id, right_id = LOCK_PAIRS[ch["group"]]
            partner_id = right_id if channel_id == left_id else left_id
            self.strips[partner_id].set_value(value)
            self.gains[partner_id] = value
            self._queue(partner_id, value)

        self._queue(channel_id, value)

    def _on_lock(self, check, group):
        self.locks[group] = check.get_active()
        if check.get_active():
            left_id, right_id = LOCK_PAIRS[group]
            left_val = self.strips[left_id].get_value()
            self.strips[right_id].set_value(left_val)
            self.gains[right_id] = left_val
            self._queue(right_id, left_val)
        self._save_all()

    def _queue(self, channel_id, value):
        for mixer, ctrl in GAIN_TO_CONTROLS.get(channel_id, []):
            self._pending[(mixer, ctrl)] = value
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(50, self._flush)

    def _flush(self):
        self._timer = None
        if not self.node_id:
            self.node_id = find_filter_chain_node_id()
            if not self.node_id:
                self._pending.clear()
                return False

        for (mixer, ctrl), val in self._pending.items():
            set_gain_runtime(self.node_id, mixer, ctrl, val)

        self._pending.clear()
        self._save_all()

        # Debounced config write (don't write on every slider tick)
        if self._config_timer:
            GLib.source_remove(self._config_timer)
        self._config_timer = GLib.timeout_add(2000, self._write_config)

        return False

    def _write_config(self):
        """Write PipeWire config file so settings survive reboots."""
        self._config_timer = None
        write_pw_config(self.gains)
        return False

    def _save_all(self):
        save_state(self.active_preset, self.locks, self.gains)

    # ── Health ──

    def _do_health_check(self):
        status = check_pipewire_status()
        self.node_id = status.filter_node_id
        self.status_label.set_markup(status.summary_markup)

        hint = status.action_hint
        if hint:
            self.hint_label.set_markup(
                f'<span color="#FF6B6B" font_family="monospace" font_size="x-small">'
                f'{hint}</span>')
            self.hint_box.set_visible(True)
        else:
            self.hint_box.set_visible(False)

        # Only show install button when needed
        self.install_box.set_visible(not status.all_good)

        return True

    # ── Install ──

    def _on_install(self, button):
        try:
            write_pw_config(self.gains)
            self._save_all()

            self.status_label.set_markup(
                '<span color="#FFE66D" font_family="monospace" font_size="small">'
                '● Restarting PipeWire...</span>')
            self.hint_box.set_visible(False)

            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)

            subprocess.run(
                ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse"],
                capture_output=True, timeout=10)

            GLib.timeout_add(2000, self._do_health_check)
        except Exception as e:
            self.status_label.set_markup(
                f'<span color="#FF6B6B" font_family="monospace" font_size="small">'
                f'✗ {e}</span>')


def main():
    app = SurroundMixerApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
