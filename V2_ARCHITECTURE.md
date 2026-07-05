# Visual Automation v2

This project is now organized around active v2 automation flows:
`template_click_sequence`, `woodcut_firemake`, and `gem_cutting`.

## Active Layout

```text
scripts/
  template_click_sequence.py      # thin CLI entrypoint
  woodcut_firemake.py             # thin CLI entrypoint
  gem_cutting.py                  # bank/withdraw/cut loop entrypoint

v2/
  definitions.py                  # shared paths and defaults
  config.py                       # JSON config loading helpers
  actions/
    mouse.py                      # repeatable actions: Bezier mouse, jitter-aware click setup
    stop_keys.py                  # Esc and Cmd+Shift+Q stop listener
    timing.py                     # delay humanization
  game_states/
    template_matching.py          # captured game state: template search result, scores, scales
    template_sequence.py          # route/template step definitions
    woodcut_firemake.py           # state checks for woodcutting, inventory, and targets
  flows/
    template_click_sequence.py    # orchestration for the active route runner
    woodcut_firemake.py           # orchestration for woodcutting and firemaking
    gem_cutting.py                # orchestration for banking and cutting gems

templates/
  template_click_sequence/        # templates owned by this script
  woodcut_firemake/               # templates owned by this script

config/
  template_click_sequence.example.json
  woodcut_firemake.example.json
  gem_cutting.example.json

legacy/
  scripts/                        # old experiments and task-specific scripts
  config/                         # old configs
  assets/templates/               # old templates
  logs/                           # old debug evidence
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
