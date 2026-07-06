# Health and fish templates

`fish.png` is the fish inventory template captured at the current RuneLite
display scale. The example configuration searches for it at scale `0.5` inside
the configured inventory region.

`references/health_bar.png` preserves the calibrated health-bar appearance at
the same source scale. It is a visual reference only; health is measured from
the bright red fill pixels rather than template-matched, so changing hitpoints
or damage numbers does not invalidate the monitor.

The monitor reads the adjacent red health bar, requires two consecutive low
readings, and then eats the best matching fish. Keep the inventory tab visible.
Start in dry-run mode and verify the reported health percentage and match score
before setting `dry_run` to `false`.
