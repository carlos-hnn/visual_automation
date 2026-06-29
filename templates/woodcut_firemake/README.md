# Woodcut Firemake Templates

Place these images in this folder before running `scripts/woodcut_firemake.py`:

- `woodcutting_status.png`: visible only while the character is woodcutting.
- `empty_inventory_slot.png`: an empty inventory slot; inventory is considered full when this is not found.
- `tree.png`: the tree target to click when not woodcutting and inventory is not full.
- `tree2.png`, `tree3.png`, `tree4.png`: optional fallback tree targets. The script tries them after `tree.png` when present.
- `tinderbox.png`: the tinderbox in inventory.
- `maple_log.png`: one maple log in inventory; also used to detect whether logs remain.
- `fire.png`: the fire target to click after selecting a log.
