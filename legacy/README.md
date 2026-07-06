# Legacy

This folder contains pre-v2 experiments plus superseded standalone flows,
configs, and wrappers retained for reference.

The active entry points are intentionally smaller:

- `scripts/combat_mode.py`
- `scripts/template_click_sequence.py`
- the other current wrappers under `scripts/`

The former standalone health/fish, prayer/potion, and Slayer-target scripts are
archived under `legacy/scripts`, `legacy/v2/flows`, and `legacy/config`. Their
fish and potion images remain in the active `templates/` tree because
`combat_mode` uses them.

Use this folder only as a reference when extracting a reusable game state,
action, setting, or template pattern into v2.
