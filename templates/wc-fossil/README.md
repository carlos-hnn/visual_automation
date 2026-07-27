# WC Fossil

This flow reuses the woodcutting status, empty-slot, deposit-all, and bank-close
templates already present in the project. Trees and the bank use cyan RuneLite
markers; the hidden-passage hole uses a green marker.

Before live use, fill both `travel_points` in `config/wc-fossil.example.json`.
Read exact screen coordinates with:

```bash
.venv/bin/python scripts/wc-fossil.py --show-mouse-position
```
