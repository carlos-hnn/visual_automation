# Visual Automation v2

Active automation is now centered on v2 flows: `template_click_sequence` and `woodcut_firemake`.

## Run

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
.venv/bin/python scripts/woodcut_firemake.py --dry-run
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
v2/
templates/template_click_sequence/
templates/woodcut_firemake/
config/template_click_sequence.example.json
config/woodcut_firemake.example.json
```

The older scripts, configs, templates, and debug evidence live under `legacy/`.
Use them only as reference material when extracting reusable states or actions into v2.

More detail: [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md).
