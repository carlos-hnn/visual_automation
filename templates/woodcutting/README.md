# Woodcutting templates

The example config currently reuses these correctly scaled images from
`templates/woodcut_firemake/`:

- `woodcutting_status.png`: visible only while the character is cutting wood.
- `empty_inventory_slot.png`: one empty inventory slot.

Logs in the inventory and tree targets in the game are detected from their cyan
RuneLite markers, so they do not need image templates.

To give this flow independent templates later, copy the two images here and set
`templates_dir` to `templates/woodcutting`.
