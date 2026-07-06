#!/bin/zsh
cd "${0:A:h}"
exec .venv/bin/python scripts/automation_control_panel.py
