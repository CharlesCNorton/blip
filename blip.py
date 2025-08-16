#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DOS-STYLE TEXT BLIP TESTER — scheduled blips, reliable playback
v1.4 — adds 10 RPG-inspired variations + slower defaults

What’s new
----------
• 10 additional “inspired-by” RPG variations (now 20 total).
• Slower default pacing: CPS=26.0, punctuation multiplier=2.2
• Same rock-solid “one audio per line” rendering; Windows defaults to winsound-file.

Menu (early-90s style, no CLI flags)
------------------------------------
  1-20) Play a single variation
  A)    Play ALL variations (scheduled, one audio per line)
  V)    Verify sound (beep)
  T)    Edit text lines
  C)    Set CPS (chars/sec)
  P)    Set punctuation pause multiplier
  W)    Toggle whitespace blips
  G)    Set post-gap seconds
  F)    Set silence-flush ms
  S)    Set amplitude (rebuild grains)
  H)    Set sample rate (44100/48000 recommended)
  K)    Deep clear audio NOW (purge/kill players)
  R)    Reset safe defaults
  L)    List variations
  Q)    Quit
  D)    (Windows only) Toggle driver: winsound-file <-> winsound-mem
"""

import io
import math
import os
import platform
import random
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from typing import List, Tuple, Optional

IS_WINDOWS = (platform.system() == "Windows")
IS_MAC = (platform.system() == "Darwin")
PUNCT = set(".!?;:")

# ======================
# Defaults / Banner
# ======================

DEFAULT_SR = 44100
DEFAULT_AMP = 0.24  # safe-but-audible
DEFAULT_CPS = 26.0  # slower default
DEFAULT_PUNCT_MULT = 2.2

BANNER = r"""
===============================================================
  TEXT BLIP TESTER v1.4      (Early-90s Command Prompt Menu)
---------------------------------------------------------------
  1-20) Play a single variation
  A)    Play ALL variations (scheduled, one audio per line)
  V)    Verify sound (beep)
  T)    Edit text lines
  C)    Set CPS (chars/sec)
  P)    Set punctuation pause multiplier
  W)    Toggle whitespace blips
  G)    Set post-GAP seconds
  F)    Set silence-FLUSH ms
  S)    Set amplitude (0..1) and rebuild
  H)    Set sample rate (44100/48000) and rebuild
  K)    Deep clear audio NOW (purge/kill players)
  R)    Reset safe defaults
  L)    List variations
  Q)    Quit
  D)    (Windows only) Toggle driver: winsound-file <-> winsound-mem
---------------------------------------------------------------
"""

# ======================
# Basic DSP
# ======================

def hann_env(n: int, total: int) -> float:
    if total <= 1: return 1.0
    u = n / (total - 1)
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * u))

def clamp(x: float, lo=-1.0, hi=1.0) -> float:
    return lo if x < lo else hi if x > hi else x

def pcm16_bytes(samples: List[float]) -> bytes:
    b = bytearray()
    for s in samples:
        b += struct.pack("<h", int(clamp(s) * 32767))
    return bytes(b)

def wav_bytes(samples: List[float], sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16_bytes(samples))
    return buf.getvalue()

def limit_peak(samples: List[float], limit_peak: float = 0.98, dc_block: bool = True) -> List[float]:
    if not samples: return samples
    if dc_block:
        m = sum(samples) / len(samples)
        samples = [s - m for s in samples]
    peak = max(1e-12, max(abs(s) for s in samples))
    if peak > limit_peak:
        scale = limit_peak / peak
        samples = [s * scale for s in samples]
    return samples

# ======================
# Grains (short blips)
# ======================

def synth_fixed(freq: float, dur_ms: float, waveform: str, duty: float,
                vibrato_rate: float, vibrato_depth: float, amp: float, sr: int) -> List[float]:
    n = int(sr * (dur_ms / 1000.0))
    out = [0.0] * n
    phase = 0.0
    for i in range(n):
        t = i / sr
        f = freq * (1.0 + vibrato_depth * math.sin(2.0 * math.pi * vibrato_rate * t)) if vibrato_rate > 0 else freq
        phase += 2.0 * math.pi * f / sr
        if phase > 1e9: phase -= 1e9
        p = (phase / (2.0 * math.pi)) % 1.0
        if waveform == "sine":
            v = math.sin(phase)
        elif waveform == "triangle":
            v = 2.0 * abs(2.0 * p - 1.0) - 1.0
        elif waveform == "saw":
            v = 2.0 * p - 1.0
        elif waveform == "pulse":
            v = 1.0 if p < duty else -1.0
        else:
            v = math.sin(phase)
        out[i] = v * hann_env(i, n) * amp
    return out

def synth_sweep(f0: float, f1: float, dur_ms: float, waveform: str, amp: float, sr: int) -> List[float]:
    n = int(sr * (dur_ms / 1000.0))
    out = [0.0] * n
    phase = 0.0
    for i in range(n):
        u = i / max(1, n - 1)
        f = f0 + (f1 - f0) * u
        phase += 2.0 * math.pi * f / sr
        p = (phase / (2.0 * math.pi)) % 1.0
        if waveform == "sine":
            v = math.sin(phase)
        elif waveform == "triangle":
            v = 2.0 * abs(2.0 * p - 1.0) - 1.0
        elif waveform == "saw":
            v = 2.0 * p - 1.0
        else:
            v = math.sin(phase)
        out[i] = v * hann_env(i, n) * amp
    return out

def synth_noise(dur_ms: float, lowpass_alpha: float, amp: float, sr: int) -> List[float]:
    n = int(sr * (dur_ms / 1000.0))
    out = [0.0] * n
    y = 0.0
    for i in range(n):
        x = (random.random() * 2.0 - 1.0)
        y = lowpass_alpha * x + (1.0 - lowpass_alpha) * y
        out[i] = y * hann_env(i, n) * amp
    return out

def synth_fm(car_freq: float, mod_ratio: float, index: float,
             dur_ms: float, amp: float, sr: int) -> List[float]:
    """Simple 2-operator FM (Genesis-like flavor)"""
    n = int(sr * (dur_ms / 1000.0))
    out = [0.0] * n
    for i in range(n):
        t = i / sr
        mod = math.sin(2.0 * math.pi * (car_freq * mod_ratio) * t)
        v = math.sin(2.0 * math.pi * car_freq * t + index * mod)
        out[i] = v * hann_env(i, n) * amp
    return out

@dataclass
class Variation:
    name: str
    grain: List[float]
    dur_ms: float

def build_variations(amp: float, sr: int) -> List[Variation]:
    V: List[Variation] = []
    def mk(samples: List[float], name: str, ms: float):
        V.append(Variation(name=name, grain=limit_peak(samples), dur_ms=ms))

    # --- Original 10 baseline timbres ---
    mk(synth_fixed(820, 55, "pulse", 0.25, 0.0, 0.0, amp, sr), "pulse25_mid", 55)
    mk(synth_fixed(920, 55, "pulse", 0.125, 0.0, 0.0, amp, sr), "pulse12_bright", 55)
    mk(synth_fixed(600, 55, "triangle", 0.25, 0.0, 0.0, amp, sr), "triangle_soft", 55)
    mk(synth_fixed(700, 55, "saw", 0.25, 0.0, 0.0, amp, sr), "saw_buzzy", 55)
    mk(synth_sweep(650, 900, 70, "sine", amp, sr), "sine_up_sweep", 70)
    mk(synth_sweep(950, 650, 70, "sine", amp, sr), "sine_down_sweep", 70)
    mk(synth_fixed(850, 60, "pulse", 0.25, 8.0, 0.06, amp, sr), "pulse_vibrato", 60)
    mk(synth_noise(40, 0.22, amp * 0.9, sr), "noise_click", 40)
    a = synth_fixed(700, 26, "sine", 0.25, 0.0, 0.0, amp, sr) + \
        synth_fixed(950, 26, "sine", 0.25, 0.0, 0.0, amp, sr)
    mk(a, "two_tone", 52)
    b = synth_fixed(500, 18, "pulse", 0.25, 0.0, 0.0, amp, sr) + \
        synth_fixed(650, 18, "pulse", 0.25, 0.0, 0.0, amp, sr) + \
        synth_fixed(820, 18, "pulse", 0.25, 0.0, 0.0, amp, sr)
    mk(b, "arp_chiptune", 54)

    # --- NEW: 10 RPG-inspired variants ---
    # (Timbral nods to the platforms/styles; not 1:1 rips)
    mk(synth_fixed(840, 45, "pulse", 0.25, 0.0, 0.0, amp * 0.95, sr), "ct_snes_pulse", 45)         # Chrono Trigger (SNES-ish)
    mk(synth_fixed(680, 50, "triangle", 0.0, 0.0, 0.0, amp, sr),         "eb_snes_blip", 50)       # EarthBound (SNES-ish)
    mk(synth_fixed(610, 55, "triangle", 0.0, 0.0, 0.0, amp, sr),         "ff6_snes_tri", 55)       # FFVI (SNES-ish)
    mk(synth_fixed(980, 60, "pulse", 0.125, 0.0, 0.0, amp, sr),          "pkmn_gb_square12", 60)   # Pokémon R/B (GB 12.5% pulse)
    mk(synth_fixed(440, 45, "pulse", 0.50, 0.0, 0.0, amp, sr),           "dq_nes_square50", 45)    # Dragon Quest (NES square)
    mk(synth_noise(24, 0.10, amp*0.80, sr) + synth_fixed(800, 18, "sine", 0.0, 0.0, 0.0, amp*0.75, sr),
       "fe_gba_click", 42)                                                                                    # Fire Emblem (GBA click+ping)
    mk(synth_fixed(720, 55, "saw", 0.0, 0.0, 0.0, amp, sr),               "gs_gba_saw", 55)        # Golden Sun (GBA brighter)
    mk(synth_sweep(500, 860, 48, "sine", amp, sr),                        "pm_n64_chirp", 48)      # Paper Mario (short chirp up)
    mk(synth_fm(600, 2.0, 1.2, 55, amp, sr),                              "ps4_gen_fm", 55)        # Phantasy Star IV (Genesis FM-ish)
    mk(synth_fixed(760, 36, "pulse", 0.25, 14.0, 0.05, amp, sr),          "ut_default_blip", 36)   # Undertale (tight vibrato)

    return V

# ======================
# Schedule & render one line buffer
# ======================

def schedule_beeps_for_line(text: str, cps: float, punct_mult: float,
                            include_whitespace: bool, grain_len: int, sr: int) -> Tuple[List[int], int]:
    starts: List[int] = []
    t = 0.0
    step = 1.0 / max(1.0, cps)
    for ch in text:
        if ch.strip() or include_whitespace:
            starts.append(int(t * sr))
        t += step * (punct_mult if ch in PUNCT else 1.0)
    total_sec = t + (grain_len / sr) + 0.12
    return starts, int(total_sec * sr) + 1

def render_line_audio(text: str, var: Variation, sr: int, cps: float,
                      punct_mult: float, include_whitespace: bool) -> List[float]:
    grain = var.grain
    glen = len(grain)
    starts, total_len = schedule_beeps_for_line(text, cps, punct_mult, include_whitespace, glen, sr)
    buf = [0.0] * total_len
    for s0 in starts:
        j_end = min(total_len, s0 + glen)
        j = s0; i = 0
        while j < j_end:
            buf[j] += grain[i]
            j += 1; i += 1
    return limit_peak(buf, limit_peak=0.95, dc_block=True)

# ======================
# Player (Windows uses winsound file by default)
# ======================

class Player:
    """
    Windows default: 'winsound-file' (temp WAV per line with SND_FILENAME|SND_ASYNC).
    Windows optional: 'winsound-mem'  (in-memory SND_MEMORY|SND_ASYNC).
    POSIX: afplay/paplay/aplay via subprocess.
    """
    def __init__(self):
        self.mode, self.cmd = self._detect()
        self._last_proc: Optional[subprocess.Popen] = None
        self._hold_bytes: Optional[bytes] = None
        self._last_path: Optional[str] = None

    def _detect(self) -> Tuple[str, List[str]]:
        if IS_WINDOWS:
            return ("winsound-file", [])
        if IS_MAC and shutil.which("afplay"):
            return ("afplay", ["afplay"])
        if shutil.which("paplay"):
            return ("paplay", ["paplay"])
        if shutil.which("aplay"):
            return ("aplay", ["aplay", "-q"])
        return ("bell", [])

    # Windows-only toggle
    def toggle_windows_driver(self):
        if not IS_WINDOWS: return
        if self.mode == "winsound-file":
            self.mode = "winsound-mem"
        else:
            self.mode = "winsound-file"

    def reset_audio(self, kill_players: bool = False):
        # Stop previous subprocess if any
        if self._last_proc and self._last_proc.poll() is None:
            try:
                self._last_proc.terminate()
                self._last_proc.wait(timeout=0.2)
            except Exception:
                try: self._last_proc.kill()
                except Exception: pass
        self._last_proc = None

        # Purge winsound & remove temp file if used
        if IS_WINDOWS:
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            if self._last_path and os.path.exists(self._last_path):
                try: os.remove(self._last_path)
                except Exception: pass
            self._last_path = None
            self._hold_bytes = None
        else:
            if kill_players:
                targets = []
                if self.mode == "afplay": targets.append("afplay")
                if self.mode == "paplay": targets.append("paplay")
                if self.mode == "aplay": targets.append("aplay")
                for nm in targets:
                    if shutil.which("pkill"):
                        try: subprocess.run(["pkill", "-x", nm], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception: pass
                    if shutil.which("killall"):
                        try: subprocess.run(["killall", "-q", nm], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception: pass

    def play_line_async(self, samples: List[float], sr: int, tmpdir: str) -> float:
        """Start playback of rendered line; return approx duration (seconds)."""
        duration = len(samples) / float(sr)
        if self.mode == "winsound-file":
            try:
                import winsound
                # Write one temp WAV per line
                path = os.path.join(tmpdir, f"line_{int(time.time()*1000)}.wav")
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                    wf.writeframes(pcm16_bytes(samples))
                self._last_path = path
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass
        elif self.mode == "winsound-mem":
            try:
                import winsound
                data = wav_bytes(samples, sr)
                self._hold_bytes = data
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
            except Exception:
                pass
        elif self.mode in ("afplay", "paplay", "aplay"):
            path = os.path.join(tmpdir, f"line_{int(time.time()*1000)}.wav")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes(pcm16_bytes(samples))
            try:
                self._last_proc = subprocess.Popen(self.cmd + [path],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._last_path = path
            except Exception:
                self._last_proc = None
        else:
            sys.stdout.write("\a"); sys.stdout.flush()
        return duration

    def wait_done(self, timeout: float):
        # winsound has no join; sleep for duration; then tidy up
        if self.mode.startswith("winsound"):
            time.sleep(max(0.0, timeout))
            # Cleanup file if used
            if self._last_path and os.path.exists(self._last_path):
                try: os.remove(self._last_path)
                except Exception: pass
            self._last_path = None
            self._hold_bytes = None
            return
        if self._last_proc:
            try:
                self._last_proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
            # Cleanup temp file after process ends
            if self._last_path and os.path.exists(self._last_path):
                try: os.remove(self._last_path)
                except Exception: pass
            self._last_path = None

    def beep_verify(self, sr: int = 44100):
        """Play a 440 Hz 300 ms test tone to verify audio path."""
        n = int(sr * 0.300)
        tone = [math.sin(2*math.pi*440*i/sr)*0.3 for i in range(n)]
        self.play_line_async(limit_peak(tone), sr, tempfile.gettempdir())
        self.wait_done(0.35)

    def label(self) -> str:
        return self.mode

# ======================
# Printing synced to schedule
# ======================

def print_line_synced(text: str, cps: float, punct_mult: float):
    step = 1.0 / max(1.0, cps)
    t0 = time.monotonic()
    t = 0.0
    for ch in text:
        target = t0 + t
        while True:
            now = time.monotonic()
            dt = target - now
            if dt <= 0: break
            time.sleep(0.001 if dt < 0.01 else 0.01)
        sys.stdout.write(ch); sys.stdout.flush()
        t += step * (punct_mult if ch in PUNCT else 1.0)
    if not text.endswith("\n"):
        print("")

# ======================
# Orchestration
# ======================

@dataclass
class Config:
    texts: List[str]
    cps: float
    punct_mult: float
    include_whitespace: bool
    post_gap: float
    silence_flush_ms: int
    amp: float
    sr: int
    deep_clear_between_runs: bool

def cls():
    os.system("cls" if os.name == "nt" else "clear")

def print_menu(vars: List[Variation], cfg: Config, player: Player):
    cls()
    print(BANNER)
    for i, v in enumerate(vars, 1):
        print(f"  {i:2d}) {v.name:16s} ({int(v.dur_ms)} ms)")
    extra = f" | Driver: {player.label()}" if IS_WINDOWS else ""
    print("\n  Device:", ("Windows" if IS_WINDOWS else "POSIX"), extra,
          " | SR:", cfg.sr, "Hz | AMP:", f"{cfg.amp:.2f}",
          " | CPS:", f"{cfg.cps:.1f}", " | Punct×", f"{cfg.punct_mult:.1f}",
          " | Gap:", f"{cfg.post_gap:.2f}s",
          " | WS:", "ON" if cfg.include_whitespace else "OFF")
    print("---------------------------------------------------------------")

def settle_device(player: Player, sr: int, ms: int):
    """Play a short silence to settle the device."""
    n = int(sr * (ms / 1000.0))
    silence = [0.0] * max(1, n)
    player.play_line_async(silence, sr, tempfile.gettempdir())
    player.wait_done((ms/1000.0) + 0.05)

def play_line(text: str, var: Variation, cfg: Config, player: Player, tmpdir: str):
    samples = render_line_audio(text, var, cfg.sr, cfg.cps, cfg.punct_mult, cfg.include_whitespace)
    duration = player.play_line_async(samples, cfg.sr, tmpdir)
    print_line_synced(text, cfg.cps, cfg.punct_mult)
    player.wait_done(duration + 0.05)
    settle_device(player, cfg.sr, cfg.silence_flush_ms)
    if cfg.post_gap > 0: time.sleep(cfg.post_gap)

def play_variation(v: Variation, cfg: Config, player: Player, tmpdir: str):
    header = f"=== {v.name}  ({int(v.dur_ms)} ms) ==="
    print(header); print("-" * len(header))
    for line in cfg.texts:
        play_line(line, v, cfg, player, tmpdir)
    print()

def play_all(vars: List[Variation], cfg: Config, player: Player, tmpdir: str):
    print(f"[Playing ALL | driver={player.label()} | sr={cfg.sr} | amp={cfg.amp:.2f}]")
    for v in vars:
        play_variation(v, cfg, player, tmpdir)

def edit_lines() -> List[str]:
    print("\nEnter text lines (blank line to finish):")
    out = []
    while True:
        try:
            s = input("> ")
        except EOFError:
            break
        if s.strip() == "":
            break
        out.append(s)
    return out or ["Link, can you hear me? The forest is whispering...",
                   "A hero rises. A legend returns."]

# ======================
# Main menu loop
# ======================

def main():
    cfg = Config(
        texts=["Link, can you hear me? The forest is whispering...",
               "A hero rises. A legend returns."],
        cps=DEFAULT_CPS,
        punct_mult=DEFAULT_PUNCT_MULT,
        include_whitespace=False,
        post_gap=0.80,
        silence_flush_ms=240,
        amp=DEFAULT_AMP,
        sr=DEFAULT_SR,
        deep_clear_between_runs=False
    )

    player = Player()
    with tempfile.TemporaryDirectory(prefix="text_blip_sched_") as td:
        variations = build_variations(cfg.amp, cfg.sr)
        # Initial verify + settle
        print("[Audio verify] Playing a short beep...")
        player.beep_verify(cfg.sr)
        settle_device(player, cfg.sr, cfg.silence_flush_ms)

        while True:
            print_menu(variations, cfg, player)
            try:
                choice = input("Choice > ").strip()
            except EOFError:
                print(); break
            if not choice:
                continue
            c = choice.lower()

            if c in ("q", "quit", "exit"):
                print("Goodbye."); break
            if c in ("l", "list"):
                continue
            if c in ("v", "verify", "beep"):
                player.beep_verify(cfg.sr)
                input("\n[Heard it? Press ENTER]")
                continue
            if c in ("a", "all"):
                play_all(variations, cfg, player, td)
                input("\n[Done. Press ENTER]")
                continue
            if c in ("t", "text", "texts"):
                cfg.texts = edit_lines(); continue
            if c in ("c", "cps"):
                try:
                    v = float(input("CPS (chars/sec): ").strip()); 
                    if v > 0: cfg.cps = v
                except Exception: pass
                continue
            if c in ("p", "punct", "punct-mult"):
                try:
                    v = float(input("Punctuation pause multiplier: ").strip()); 
                    if v > 0: cfg.punct_mult = v
                except Exception: pass
                continue
            if c in ("w", "whitespace"):
                cfg.include_whitespace = not cfg.include_whitespace
                print("Whitespace blips:", "ON" if cfg.include_whitespace else "OFF")
                time.sleep(0.4); continue
            if c in ("g", "gap", "post-gap"):
                try:
                    v = float(input("Post-gap seconds: ").strip()); 
                    if v >= 0: cfg.post_gap = v
                except Exception: pass
                continue
            if c in ("f", "flush", "silence-flush"):
                try:
                    v = int(input("Silence flush length (ms): ").strip()); 
                    v = max(10, min(2000, v))
                    cfg.silence_flush_ms = v
                except Exception: pass
                continue
            if c in ("s", "amp", "volume"):
                try:
                    v = float(input("Amplitude 0..1 (e.g., 0.20): ").strip())
                    v = max(0.01, min(1.0, v))
                    cfg.amp = v
                    variations = build_variations(cfg.amp, cfg.sr)
                    settle_device(player, cfg.sr, cfg.silence_flush_ms)
                    print(f"Amplitude set to {cfg.amp:.2f}.")
                except Exception: pass
                time.sleep(0.4); continue
            if c in ("h", "sr", "samplerate"):
                try:
                    v = int(input("Sample rate (44100 or 48000 recommended): ").strip())
                    if v >= 8000 and v <= 192000:
                        cfg.sr = v
                        variations = build_variations(cfg.amp, cfg.sr)
                        settle_device(player, cfg.sr, cfg.silence_flush_ms)
                        print(f"Sample rate set to {cfg.sr} Hz.")
                except Exception: pass
                time.sleep(0.4); continue
            if c in ("k", "kill", "clear"):
                print("\nDeep clearing audio …")
                player.reset_audio(kill_players=True)
                settle_device(player, cfg.sr, cfg.silence_flush_ms)
                print("Done."); time.sleep(0.5); continue
            if c in ("d", "driver") and IS_WINDOWS:
                player.toggle_windows_driver()
                print("Windows driver set to:", player.label())
                player.reset_audio(kill_players=False)
                settle_device(player, cfg.sr, cfg.silence_flush_ms)
                time.sleep(0.5); continue

            # numeric selection?
            if choice.isdigit():
                i = int(choice)
                if 1 <= i <= len(variations):
                    play_variation(variations[i-1], cfg, player, td)
                    input("\n[Done. Press ENTER]")
                else:
                    print("Invalid selection."); time.sleep(0.7)
                continue

            # name selection?
            name_map = {v.name.lower(): v for v in variations}
            if c in name_map:
                play_variation(name_map[c], cfg, player, td)
                input("\n[Done. Press ENTER]")
                continue

            print("Unrecognized option."); time.sleep(0.7)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
