# Powermining

This flow does not require image templates.

It detects the green mining status text and color RuneLite markers in three
configured regions:

- `mining_status`: top-left status text area.
- `rock_markers`: cyan rocks in the game area, used when `rock_points` is empty.
- `inventory_ore_markers`: cyan ores in the inventory.

When `mining_status` is green, the script waits and polls again. When that
status is not green, it checks the chat area for the inventory-full message:
`Your inventory is too full to hold any more iron.` If that visual template is
present, it drops every cyan-marked inventory ore. Otherwise it captures the
current cyan rock markers and clicks exactly one cyan rock. The next rock click
only happens after the status returns to not mining.

`inventory_full_marker_count` remains as a fallback, but the chat message is the
primary full-inventory signal.

Before dropping, the script waits `pre_drop_status_confirm_ticks` and checks the
mining status again. If the green mining status is active, it skips the
inventory clicks. After a rock click, it polls for the green mining status using
`post_rock_mining_confirm_ticks`; this prevents chaining clicks based on stale
rock positions.

Fixed `rock_points` are ignored by the live powermining flow. Rocks must be
cyan-detected at click time.

If the script is not mining and no cyan rock marker is available, it drops the
cyan-marked inventory ores before polling again.

Large cyan rock overlays can merge into one component. When
`rock_split_large_markers` is enabled, the script splits that large color mass
into `rock_click_count` click targets.

`max_status_polls` is set in the example config to keep dry-runs finite while
the green mining status is active. Set it to `0` for unlimited live polling.

Use calibration before live runs:

```bash
.venv/bin/python scripts/powermining.py --calibrate
```
