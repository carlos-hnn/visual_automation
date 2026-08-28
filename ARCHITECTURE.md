# Visual Automation Architecture

This project is organized around small orchestration flows backed by shared state,
action, timing, geometry, marker, template, and configuration services. A flow
should describe the game-specific state machine; reusable detection and clicking
behavior belongs in the shared package.

## Active Layout

```text
scripts/
  template_click_sequence.py      # thin CLI entrypoint
  woodcut_firemake.py             # thin CLI entrypoint
  wc_fossil.py                    # Fossil Island bank route entrypoint
  combat_mode.py                  # combat, health, prayer, and target flow
  gem_cutting.py                  # bank/withdraw/cut loop entrypoint
  steel_cannonball.py             # bank/furnace loop entrypoint
  fletching_logs.py               # bank/knife/log fletching loop entrypoint
  powermining.py                  # cyan-marker rock mining and inventory dropping
  herblore.py                     # ranarr/vial mixing and static bank withdrawal loop
  potion_fill.py                  # green-tag potion pairing and static bank refill loop
  cleaning_herbs.py               # fixed-grid herb cleaning with visually confirmed bank states
  hunter.py                       # cyan waypoint following with periodic inventory clearing

src/visual_automation/              # installable application package
  core/                         # low-level capture, input, vision, and safety
  definitions.py                  # shared paths and defaults
  config.py                       # JSON config loading helpers
  runtime.py                      # migration-safe shared flow helpers
  panel_schema.py                 # operational/advanced control classification
  platforming.py                  # OS/template platform selection
  template_config.py              # shared template regions, offsets, scales, window lookup
  actions/
    bank.py                       # reusable deposit/close operations
    markers.py                    # marker selection, waiting, and stable-click policy
    mouse.py                      # repeatable actions: Bezier mouse, jitter-aware click setup
    stop_keys.py                  # global stop keys and 30-second missing-target watchdog
    timing.py                     # delay humanization
  game_states/
    bank.py                       # shared bank-open status
    combat.py                     # shared health, prayer, and combat activity states
    inventory.py                  # shared inventory-full/empty-slot status
    color_markers.py              # shared HSV marker detection for cyan/green tags
    template_state.py             # shared TemplateState and matcher state
    template_matching.py          # captured game state: template search result, scores, scales
    template_sequence.py          # route/template step definitions
    woodcut_firemake.py           # backward-compatible alias for template matcher state
  flows/
    template_click_sequence.py    # orchestration for the active route runner
    woodcut_firemake.py           # orchestration for woodcutting and firemaking
    wc_fossil.py                  # hidden-passage travel and banking route
    gem_cutting.py                # orchestration for banking and cutting gems
    fletching_logs.py             # orchestration for banking and fletching logs
    powermining.py                # orchestration for marker-based powermining

templates/
  shared/bank/                    # canonical deposit-all and common bank-close assets
  template_click_sequence/        # templates owned by this script
  woodcut_firemake/               # templates owned by this script
  wc_fossil/                      # route notes; shared templates are reused
  combat_mode/                    # combat docs and target flow notes
  fletching_logs/                 # fletching templates
  powermining/                    # marker-only powermining notes
  gem_cutting/                    # gem cutting templates
  health_fish/                    # health/food templates
  prayer_potions/                 # prayer potion templates
  steel_cannonball/               # steel cannonball templates

config/
  shared_defaults.json            # inherited integration, movement, and timing defaults
  template_click_sequence.example.json
  woodcut_firemake.example.json
  wc_fossil.example.json
  combat_mode.example.json
  gem_cutting.example.json
  steel_cannonball.example.json
  fletching_logs.example.json
  powermining.example.json

```

## Concepts

Every JSON config inherits `config/shared_defaults.json`. Script configs contain
only flow-specific behavior or explicit overrides. Runtime configs written by the
control panel likewise omit values equal to the shared defaults.

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
- color-marker selection (`best`, `nearest`, `leftmost`, `rightmost`);
- stable-marker validation with a fresh capture immediately before the click;
- bank deposit and close operations.

The control panel keeps operational parameters visible and places regions, HSV
ranges, thresholds, templates, polling, timeouts, and input integration under a
collapsed Advanced settings section. The classification lives in
`panel_schema.py`, not in browser code.

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

Project metadata lives in `pyproject.toml`; runtime dependencies are mirrored
in `requirements.txt` for simple environment setup, and development-only tools
are listed in `requirements-dev.txt`.

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

For a new active script, reuse canonical assets from `templates/shared` and create
a dedicated template folder only for flow-specific images:

```text
templates/<script_name>/
```

Then put the script's defaults/config in:

```text
config/<script_name>.example.json
```
