# Project review

## Cleaned now

- Centralized template metadata helpers in `v2/template_config.py`.
- Centralized platform selection in `v2/platforming.py`.
- Centralized reusable states:
  - `v2/game_states/bank.py`
  - `v2/game_states/combat.py`
  - `v2/game_states/inventory.py`
  - `v2/game_states/template_state.py`
- Removed project `__pycache__` folders and `.pyc` files outside `.venv`.
- Added `fletching_logs` to the control panel.
- Removed `legacy/`, unused `assets/`, empty `records/`, generated runtime
  config, and generated logs.

## Current platform convention

Use `--platform auto|mac|windows` on active scripts.

Template lookup order:

1. `template_dirs_by_platform[platform]`, if configured and present.
2. `<templates_dir>/<platform>/`, if present.
3. Base `templates_dir`.

This lets macOS templates remain in the current folders and Windows templates
live under folders such as `templates/fletching_logs/windows/`.

## Likely next optimizations

- Move duplicated `click_match`, `find_and_click`, and tick-wait helpers from
  flows into shared action modules.
- Move cyan marker detection from `fletching_logs.py` into a shared color-marker
  game state/action because combat and fletching both use marked inventory/game
  objects now.
- Add a shared bank action helper for: open bank, deposit all, withdraw item,
  close bank.
- Add Windows calibration docs/scripts for capturing template sets under
  `templates/<script>/windows/`.
- Update `ARCHITECTURE.md` whenever a flow graduates from one-off logic into
  shared state/action modules.

## Candidate trash or archive material

- `logs/debug/` contains generated screenshots/calibration evidence. Keep only
  images that are actively useful for template calibration; otherwise it can be
  cleaned regularly.
- Runtime configs under `config/runtime/` are machine/session-specific and should
  not be treated as canonical examples.
