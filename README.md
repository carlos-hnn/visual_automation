# Visual Automation v2

Active automation is available through a local control panel or the individual
v2 command-line wrappers.

## Control panel

Double-click `Automation Control Panel.command` in Finder. It opens a local
browser app where you can:

- select any active automation;
- edit every JSON parameter, including nested regions and thresholds;
- save settings without changing the example configs;
- start and stop one automation at a time;
- view live script output;
- confirm explicitly before starting a configuration with `dry_run: false`.

Saved app settings live under `config/runtime/`.

Each automation exposes a `mouse_backend` setting. `standard` uses the normal
visible cursor. `quartz` is an experimental macOS background-click backend that
posts left-click events without updating the visible cursor; keep `dry_run`
enabled until the target application has been tested deliberately.

## Run

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
.venv/bin/python scripts/woodcut_firemake.py --dry-run
.venv/bin/python scripts/combat_mode.py --dry-run
```

Using the example config:

```bash
.venv/bin/python scripts/template_click_sequence.py --config config/template_click_sequence.example.json
.venv/bin/python scripts/woodcut_firemake.py --config config/woodcut_firemake.example.json
```

Remove `--dry-run` or pass `--no-dry-run` only when you intentionally want live mouse clicks.

## Active Structure

```text
scripts/template_click_sequence.py
scripts/woodcut_firemake.py
scripts/combat_mode.py
scripts/gem_cutting.py
scripts/steel_cannonball.py
v2/
templates/template_click_sequence/
templates/woodcut_firemake/
config/template_click_sequence.example.json
config/woodcut_firemake.example.json
```

The older scripts, configs, templates, and debug evidence live under `legacy/`.
Use them only as reference material when extracting reusable states or actions into v2.

More detail: [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md).
