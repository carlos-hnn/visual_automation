# Gem-cutting templates

These Retina-resolution templates are matched at scale `0.5` by
`config/gem_cutting.example.json`.

- `bank.png`: closed bank booth in the calibrated camera view.
- `deposit_all.png`: bank deposit-inventory control.
- `bank_close.png`: bank window close control. Closing the bank is required
  before chisel-on-gem can work.
- `gem_blue.png`, `gem_red.png`, `gem_green.png`: uncut gem variants.
- `bank_gem_blue.png`, `bank_gem_red.png`: sprite-only bank templates whose
  crops exclude the changing stack-count digits.
- `chisel.png`: chisel in the locked first inventory slot.
- `cut_confirmation_<color>.png`: color-specific gem icon in the cutting confirmation panel.
  The flow waits for and clicks this instead of relying on a timed Space press.
- `empty_inventory_slot.png`: retained calibration reference.

Use `scripts/gem_cutting.py --calibrate` whenever the RuneLite window size,
camera, interface scaling, or inventory layout changes.

After clicking the cutting confirmation icon, the flow waits 52 full game ticks
before checking the inventory. This prevents the final small uncut stack from
being mistaken for an empty inventory.
