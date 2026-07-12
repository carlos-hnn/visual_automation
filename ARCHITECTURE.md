# Visual Automation Architecture

This project is organized around active automation flows:
`template_click_sequence`, `woodcut_firemake`, `gem_cutting`, `steel_cannonball`,
`combat_mode`, and `fletching_logs`.

## Active Layout

```text
scripts/
  template_click_sequence.py      # thin CLI entrypoint
  woodcut_firemake.py             # thin CLI entrypoint
  combat_mode.py                  # combat, health, prayer, and target flow
  gem_cutting.py                  # bank/withdraw/cut loop entrypoint
  steel_cannonball.py             # bank/furnace loop entrypoint
  fletching_logs.py               # bank/knife/log fletching loop entrypoint

v2/                              # main automation package; name kept for import stability
  definitions.py                  # shared paths and defaults
  config.py                       # JSON config loading helpers
  platforming.py                  # OS/template platform selection
  template_config.py              # shared template regions, offsets, scales, window lookup
  actions/
    mouse.py                      # repeatable actions: Bezier mouse, jitter-aware click setup
    stop_keys.py                  # Esc and Cmd+Shift+Q stop listener
    timing.py                     # delay humanization
  game_states/
    bank.py                       # shared bank-open status
    combat.py                     # shared health, prayer, and combat activity states
    inventory.py                  # shared inventory-full/empty-slot status
    template_state.py             # shared TemplateState and matcher state
    template_matching.py          # captured game state: template search result, scores, scales
    template_sequence.py          # route/template step definitions
    woodcut_firemake.py           # backward-compatible alias for template matcher state
  flows/
    template_click_sequence.py    # orchestration for the active route runner
    woodcut_firemake.py           # orchestration for woodcutting and firemaking
    gem_cutting.py                # orchestration for banking and cutting gems
    fletching_logs.py             # orchestration for banking and fletching logs

templates/
  template_click_sequence/        # templates owned by this script
  woodcut_firemake/               # templates owned by this script
  combat_mode/                    # combat docs and target flow notes
  fletching_logs/                 # fletching templates
  gem_cutting/                    # gem cutting templates
  health_fish/                    # health/food templates
  prayer_potions/                 # prayer potion templates
  steel_cannonball/               # steel cannonball templates

config/
  template_click_sequence.example.json
  woodcut_firemake.example.json
  combat_mode.example.json
  gem_cutting.example.json
  steel_cannonball.example.json
  fletching_logs.example.json

```

## Concepts

Game states are things we observe from the screen:

- template match found/not found;
- best observed score while waiting;
- template scale that matched best;
- captured frame and debug annotation.

Actions are repeatable operations that can be reused by future scripts:

- Bezier mouse movement;
- click jitter and point tolerance;
- timing jitter;
- pre-click delays;
- stop-key handling.

Definitions are stable project concepts:

- project root;
- active template folder;
- route step order;
- default thresholds, waits, scales, and movement timings.

Platform support is centralized:

- each active script accepts `--platform auto|mac|windows`;
- `auto` detects the current OS;
- configs may define `template_dirs_by_platform`;
- otherwise `templates/<script>/windows/` is used when present;
- if no platform-specific folder exists, scripts fall back to the base template
  folder.

Generated files that should stay out of source control:

- `__pycache__/` and `*.pyc`;
- `config/runtime/` panel/session configs;
- `logs/debug/` screenshots;
- one-off calibration evidence unless it is intentionally promoted into a
  template folder.

## Running

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
```

With config:

```bash
.venv/bin/python scripts/template_click_sequence.py --config config/template_click_sequence.example.json
```

Calibrate and run gem cutting:

```bash
.venv/bin/python scripts/gem_cutting.py --calibrate
.venv/bin/python scripts/gem_cutting.py --gem green --no-dry-run --loops 0
```

For a new active script, create a dedicated template folder:

```text
templates/<script_name>/
```

Then put the script's defaults/config in:

```text
config/<script_name>.example.json
```
