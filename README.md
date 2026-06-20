# Visual Automation v2

Active automation is now centered on `template_click_sequence`.

## Run

```bash
.venv/bin/python scripts/template_click_sequence.py --dry-run
```

Using the example config:

```bash
.venv/bin/python scripts/template_click_sequence.py --config config/template_click_sequence.example.json
```

Remove `--dry-run` or pass `--no-dry-run` only when you intentionally want live mouse clicks.

## Active Structure

```text
scripts/template_click_sequence.py
v2/
templates/template_click_sequence/
config/template_click_sequence.example.json
```

The older scripts, configs, templates, and debug evidence live under `legacy/`.
Use them only as reference material when extracting reusable states or actions into v2.

More detail: [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md).
