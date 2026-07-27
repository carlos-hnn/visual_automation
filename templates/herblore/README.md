# Herblore

This flow detects green ranarr and red vial tags in the inventory, combines one
pair, presses Space, waits 15 ticks, then uses the blue game-area bank marker.

The shared `steel_cannonball` bank, deposit-all, and close templates are reused.
The two withdrawal items are selected with `--bank-item-1` and `--bank-item-2`
(or `bank_item_1_name` / `bank_item_2_name` in JSON). A saved, window-relative
point under `bank_item_points` is reused. If a normalized item key has no point,
the first open tagged bank is scanned by hover/OCR without using bank search.
Pass `--calibrate-position` to ignore both saved points and identify both items
again on the first open bank.
