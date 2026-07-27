#!/bin/zsh
cd "${0:A:h}"
external_python="$HOME/Library/Application Support/visual_automation/venv/bin/python"
if [[ -x "$external_python" ]]; then
  exec "$external_python" scripts/automation_control_panel.py
fi
exec .venv/bin/python scripts/automation_control_panel.py
