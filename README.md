# AquaReminder

A glassmorphic macOS status-bar hydration companion. It lives quietly in the menu
bar and, on a timer, slides a frosted-glass HUD up from the bottom-right corner
with a little pixel character who drinks water with you.

## Features

- **Menu-bar only** — no dock icon, no window clutter
- **Frosted-glass HUD** that slides in on a timer and tracks your daily count
- **Animated pixel companion** rendered from a sprite sheet, who jumps to
  celebrate when you log a glass
- **Sound** — a synthesized water-pour cue when the reminder appears and a cheer
  arpeggio when the character jumps; both can be replaced with your own audio
  files or muted from the menu
- **Light and dark mode** aware, following the system appearance
- Configurable interval: 30 minutes, 1 hour, 1 hour 30 minutes, or 2 hours

## Requirements

- macOS
- Python 3.9+
- PyQt6

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 app.py            # normal run (lives in the macOS status bar)
python3 app.py --demo     # run and pop the reminder immediately
python3 app.py --smoke    # headless self-test: exercises every paint path
```

Once running, click the menu-bar icon to log a glass, change the reminder
interval, swap the sounds, or reset today's count.

## Project layout

```
app.py                      the whole application
assets/
  buddy.png                 sprite sheet (generated)
  buddy.json                frame size + named animation segments (generated)
  water_pour.wav            default reminder sound (generated)
  cheer.wav                 default celebration sound (generated)
tools/
  build_sprites.py          character_animation.gif  -> assets/buddy.{png,json}
  make_water_sound.py       synthesizes assets/water_pour.wav
  make_cheer_sound.py       synthesizes assets/cheer.wav
  _audio.py                 shared DSP + WAV writing helpers
character_animation.gif     source animation for the sprite sheet
```

## Regenerating assets

The files in `assets/` are committed, so you only need this after changing a
source. Run from the repo root:

```bash
python3 tools/build_sprites.py       # after editing character_animation.gif
python3 tools/make_water_sound.py
python3 tools/make_cheer_sound.py
```

The sounds are synthesized from scratch rather than downloaded, which keeps the
repo self-contained and the results reproducible.

## License

MIT — see [LICENSE](LICENSE).
