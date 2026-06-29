# Visual Automation v2

This project is now organized around active v2 automation flows:
`template_click_sequence` and `woodcut_firemake`.

## Active Layout

```text
scripts/
  template_click_sequence.py      # thin CLI entrypoint
  woodcut_firemake.py             # thin CLI entrypoint

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

templates/
  template_click_sequence/        # templates owned by this script
  woodcut_firemake/               # templates owned by this script

config/
  template_click_sequence.example.json
  woodcut_firemake.example.json

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

For a new active script, create a dedicated template folder:

```text
templates/<script_name>/
```

Then put the script's defaults/config in:

```text
config/<script_name>.example.json
```
