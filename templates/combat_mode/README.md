# Combat mode

This flow detects the bright red RuneLite outlines applied to Slayer targets;
it does not require a monster image template. It selects the detected outline
nearest the configured character anchor.

Combat is active whenever the configured activity bar contains enough green
pixels. After an attack click, the flow waits for that green confirmation and
will not retry until `attack_confirm_timeout` expires.

Health, combat, and target selection are evaluated on one five-second interval.
When an active combat bar disappears, the flow waits one additional second and
checks the bar again before searching for another target, allowing the defeated
NPC's red outline time to disappear.

The same loop monitors health and prayer every five seconds. Health has first
priority, prayer second, and selecting another target third. Fish templates
come from `templates/health_fish`; potion templates come from
`templates/prayer_potions`. After a live consumable click, the pointer is parked
outside the inventory so RuneLite's hover preview does not distort the next
resource reading.

The monitors can be toggled independently with `health_monitor_enabled` and
`prayer_monitor_enabled` in the config, or with `--health-monitor`,
`--no-health-monitor`, `--prayer-monitor`, and `--no-prayer-monitor`. Prayer is
disabled by default for the current inventory. Its original `0.78` match
threshold is preserved so other potion shapes are not treated as prayer doses.

Run `scripts/combat_mode.py --calibrate --dry-run` first. The command is
read-only, saves an annotated image in `logs/debug`, and exits. Green marks the
chosen target, yellow marks other candidates, and the cyan cross is the
character anchor. Keep `dry_run` enabled until those positions are correct.
