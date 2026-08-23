# Visual Automation

Active automation is available through a local control panel or individual
command-line wrappers.

## Setup

Python 3.11 or newer is supported. A virtual environment may live at `.venv/`
for convenience, but it is local machine state and is intentionally ignored by
Git. It may also live elsewhere (the Finder launcher checks
`~/Library/Application Support/visual_automation/venv` first).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Control panel

Double-click `Automation Control Panel.command` in Finder. It opens a local
browser app where you can:

- select any active automation;
- edit the operational parameters directly;
- expand **Advanced settings** only when calibrating regions, colors, templates,
  thresholds, jitter, or integration details;
- save settings without changing the example configs;
- start and stop one automation at a time;
- view live script output;
- confirm explicitly before starting a configuration with `dry_run: false`.

Saved app settings live under `config/runtime/`, which is generated locally.
Common defaults are inherited from `config/shared_defaults.json`; runtime files
store only values that differ from those defaults.

Each automation exposes a `mouse_backend` setting. `standard` uses the normal
visible cursor. `quartz` is an experimental macOS background-click backend that
posts left-click events without updating the visible cursor; keep `dry_run`
enabled until the target application has been tested deliberately.

## Run

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
.venv/bin/python scripts/woodcut_firemake.py --dry-run
.venv/bin/python scripts/woodcutting.py --calibrate
.venv/bin/python scripts/woodcutting.py --dry-run
.venv/bin/python scripts/wc_fossil.py --show-mouse-position
.venv/bin/python scripts/wc_fossil.py --dry-run
.venv/bin/python scripts/combat_mode.py --dry-run
.venv/bin/python scripts/fletching_logs.py --dry-run
.venv/bin/python scripts/powermining.py --dry-run
.venv/bin/python scripts/motherlode_mine.py --calibrate
.venv/bin/python scripts/motherlode_mine.py --dry-run
.venv/bin/python scripts/mine_bank.py --calibrate
.venv/bin/python scripts/mine_bank.py --dry-run
.venv/bin/python scripts/blast_furnace.py --dry-run
.venv/bin/python scripts/cleaning_herbs.py --calibrate
.venv/bin/python scripts/cleaning_herbs.py --dry-run
```

Using the example config:

```bash
.venv/bin/python scripts/template_click_sequence.py --config config/template_click_sequence.example.json
.venv/bin/python scripts/woodcut_firemake.py --config config/woodcut_firemake.example.json
```

Remove `--dry-run` or pass `--no-dry-run` only when you intentionally want live mouse clicks.

## Global safety

Every active script uses the same safety supervisor:

- `Esc` stops the automation through a global keyboard listener, even when the
  terminal or VS Code is not focused. On macOS, the Python/Terminal host must
  retain Accessibility permission.
- Repeated unsuccessful visual searches arm a 30-second watchdog. A successful
  target/state detection clears it; if no visual progress occurs before the
  deadline, the script requests a stop and prints the last missing target.
- Long, expected production waits do not arm the watchdog by themselves.

`Cmd+Shift+Q`, `Cmd+C`, and `Ctrl+C` remain supported as additional stop keys.

All active scripts accept `--platform auto|mac|windows`. `auto` detects the
current OS. If `templates/<script>/windows/` exists, Windows runs use that
template set; otherwise the script falls back to the base template folder.

## Active Structure

```text
scripts/template_click_sequence.py
scripts/woodcut_firemake.py
scripts/woodcutting.py
scripts/wc_fossil.py
scripts/combat_mode.py
scripts/gem_cutting.py
scripts/steel_cannonball.py
scripts/blast_furnace.py
scripts/fletching_logs.py
scripts/powermining.py
scripts/motherlode_mine.py
scripts/mine_bank.py
scripts/cleaning_herbs.py
scripts/herblore.py
scripts/potion_fill.py
src/visual_automation/              # installable application package
templates/shared/                   # canonical assets reused by flows
templates/template_click_sequence/
templates/woodcut_firemake/
templates/woodcutting/
templates/wc_fossil/
templates/fletching_logs/
templates/powermining/
config/template_click_sequence.example.json
config/woodcut_firemake.example.json
config/woodcutting.example.json
config/wc_fossil.example.json
config/fletching_logs.example.json
config/powermining.example.json
config/motherlode_mine.example.json
config/mine_bank.example.json
config/herblore.example.json
config/potion_fill.example.json
config/shared_defaults.json
```

More detail: [ARCHITECTURE.md](ARCHITECTURE.md).
