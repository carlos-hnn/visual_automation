# Cleaning Herbs

Withdraws a configured grimy herb from the currently open tagged bank tab,
closes the bank, and clicks one green-tagged inventory herb per tick. When no
green herb markers remain, it opens the bank, deposits all, and repeats.

The script starts by opening/recognizing the bank, depositing the current
inventory, withdrawing the configured herb, and closing the bank. A saved point
under `bank_item_points` is reused without calibration. Only items with no saved
X/Y trigger a hover/OCR scan; bank search is never used. `loops` counts
inventories that were fully cleaned and deposited. Set `loops` to `0` to
continue until a global stop key or watchdog.

Override the item from the terminal with, for example,
`--bank-item-name "Grimy kwuarm"`. Items without a saved X/Y are resolved on
the first open bank in the same way.
