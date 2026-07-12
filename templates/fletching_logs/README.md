# Fletching logs templates

This flow fletches one inventory of maple logs into bows.

- `deposit_all.png`: bank deposit-inventory control.
- `bank_maple_log.png`: maple log item inside the bank.
- `bank_close.png`: bank window close control.
- `knife.png`: knife in inventory.
- `fletching_status_icon.png`: small status reference only; the flow primarily
  waits the configured `80` fletching ticks because the status bar changes each
  tick.

The bank target and inventory log target are selected by cyan marker detection,
not by template matching. Keep the bank booth and one log visibly marked cyan.
