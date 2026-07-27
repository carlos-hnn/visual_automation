# Potion Fill

Pairs two green-tagged inventory potions, waits one tick between clicks and two
ticks after filling. When fewer than two tagged potions remain, it banks the
inventory, withdraws the single item selected by `bank_item_name` from its
calibrated static bank position, closes the bank, and repeats. Change the config
to `super_attack_3` when processing that potion type.

With `--calibrate-position`, the first open bank is confirmed and each configured
visible slot is hovered without clicking. OCR reads the RuneLite hover text and
selects the slot whose name matches `bank_item_name`. Keep the tagged bank tab
open with the desired item visible; calibration never uses the bank search.
