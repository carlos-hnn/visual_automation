# Visual Automation

Active automation is available through a local control panel or individual
command-line wrappers.

## Control panel

Double-click `Automation Control Panel.command` in Finder. It opens a local
browser app where you can:

- select any active automation;
- edit every JSON parameter, including nested regions and thresholds;
- save settings without changing the example configs;
- start and stop one automation at a time;
- view live script output;
- confirm explicitly before starting a configuration with `dry_run: false`.

Saved app settings live under `config/runtime/`, which is generated locally.

Each automation exposes a `mouse_backend` setting. `standard` uses the normal
visible cursor. `quartz` is an experimental macOS background-click backend that
posts left-click events without updating the visible cursor; keep `dry_run`
enabled until the target application has been tested deliberately.

## Run

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
.venv/bin/python scripts/woodcut_firemake.py --dry-run
.venv/bin/python scripts/combat_mode.py --dry-run
.venv/bin/python scripts/fletching_logs.py --dry-run
```

Using the example config:

```bash
.venv/bin/python scripts/template_click_sequence.py --config config/template_click_sequence.example.json
.venv/bin/python scripts/woodcut_firemake.py --config config/woodcut_firemake.example.json
```

Remove `--dry-run` or pass `--no-dry-run` only when you intentionally want live mouse clicks.

All active scripts accept `--platform auto|mac|windows`. `auto` detects the
current OS. If `templates/<script>/windows/` exists, Windows runs use that
template set; otherwise the script falls back to the base template folder.

## Active Structure

```text
scripts/template_click_sequence.py
scripts/woodcut_firemake.py
scripts/combat_mode.py
scripts/gem_cutting.py
scripts/steel_cannonball.py
scripts/fletching_logs.py
v2/                              # main automation package; name kept for import stability
templates/template_click_sequence/
templates/woodcut_firemake/
templates/fletching_logs/
config/template_click_sequence.example.json
config/woodcut_firemake.example.json
config/fletching_logs.example.json
```

More detail: [ARCHITECTURE.md](ARCHITECTURE.md).
