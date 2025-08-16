# BLIP

_A tiny, DOS‑style tool that gives text the classic RPG “talk blip.”_

BLIP is a deliberately small, dependency‑free script that restores something
missing from modern LLMs and their wrappers: **atmosphere**—the subtle,
per‑character bleeps that made early RPG text feel alive. This project is a
**steady, incremental integration** of that experience into contemporary text
UIs, with more iterations to come.

---

## What it is

- A single script (`blip.py`) that streams your text while playing short,
  retro‑style “blips” mapped to each printed character.
- **20 variations** (10 classic + 10 RPG‑inspired timbres) you can audition from
  an early‑90s command‑prompt menu.  
- **Stable transport:** the audio for each line is **pre‑rendered as one buffer**
  (scheduled blips stamped into a single track) and played once, so there’s no
  per‑character device thrash.

> The goal: make LLM text feel less sterile and more like a living cartridge
> RPG—without any extra setup or dependencies.

---

## Why (the gap this tries to close)

Modern LLM shells tend to be all content, no **texture**. Classic RPGs layered
**tiny UI bleeps** and **timed cadence** onto text to create mood, pacing, and
presence. BLIP is an attempt to re‑introduce that: simple, tactile feedback that
improves legibility and vibe during generation/streaming.

---

## How it works (in one minute)

- You pick a variation (e.g., a triangle blip, FM ping, GB‑style 12.5% pulse).
- For each line of text, BLIP **pre‑renders** one audio buffer and places short
  “grain” blips at the **exact timestamps** the characters will appear.
- It plays that single buffer while printing characters in sync.  
  → **No** per‑char subprocesses or device re-opens; **no** audio corruption.

Platforms:
- **Windows:** one temp WAV per line via `winsound` (async), cleaned afterward.
- **macOS/Linux:** one temp WAV per line via `afplay` / `paplay` / `aplay`.
- No extra packages required.

---

## Features

- **Menu‑driven (no CLI flags):** early‑90s style UI—press keys to audition,
  tweak CPS (characters/sec), punctuation pauses, whitespace behavior, etc.
- **20 variations:** classic chip styles and “inspired‑by” RPG vibes (not rips).
- **Stability tools:** silence flush, deep clear, and generous inter‑run gaps.
- **Configurable feel:** CPS, punctuation multiplier, amplitude, sample rate
  (44.1k or 48k), whitespace blips on/off.

---

## Quick start

1. Save the script as `blip.py` (or keep your current filename).
2. Run it:
```

python blip.py

```
3. Use the menu:
- `1–20` to play a single blip style  
- `A` to play **all**  
- `C` to adjust CPS (default is intentionally **slow** to feel more “RPG”)  
- `P` to tune punctuation pauses  
- `S` amplitude, `H` sample rate, `K` deep clear, `V` verify sound

Tip: If your audio device runs at 48 kHz, set **H → 48000** to minimize any
driver resampling.

---

## Inspired‑by palette (examples)

- NES/GB‑style squares (12.5%/50%), soft triangles, short up‑chirps
- SNES‑ish mellow pings, GBA click‑plus‑ping blends
- Genesis‑flavored light FM pings (simple 2‑op)

> These are **original grains** designed to evoke the feel—no extracted assets.

---

## Roadmap

BLIP is intentionally small and evolving. Upcoming explorations:

- **Voice sets**: rotate 2–4 grains per character for richer “talk” textures  
- **Per‑variation pacing**: CPS/pauses tailored to each style  
- **Stream API hooks**: drop‑in function to drive blips from an LLM’s token stream  
- **Optional in‑process audio** (single output stream, zero temp files) for POSIX

Have an idea or a favorite game’s “text voice” you want evoked? Open an issue or
share a sample; we’ll prototype a grain and add it to the set.

---

## License

MIT. Do what you like—keep the vibe alive.

---

## Credit

Built as a practical answer to the question:  
**“What’s most missing from LLMs?”** → _The little sounds that made text feel human._

Long live the blip.
