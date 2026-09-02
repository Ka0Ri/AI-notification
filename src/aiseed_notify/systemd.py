"""Render a .service / .timer pair from a config's `schedule` block."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE = """[Unit]
Description={description}
After=network.target

[Service]
Type=oneshot
ExecStart={python} -m aiseed_notify agent {config}
{extra_lines}StandardOutput=journal
StandardError=journal
"""

TIMER = """[Unit]
Description={description} timer ({on_calendar})

[Timer]
OnCalendar={on_calendar}
Persistent={persistent}

[Install]
WantedBy=timers.target
"""


def render(cfg: dict) -> dict[str, str]:
    sched = cfg["schedule"]
    if not sched.get("on_calendar"):
        raise ValueError(f"{cfg['name']}: schedule.on_calendar is required to generate units")

    # sys.executable pins the interpreter that generated the unit, so the unit
    # keeps working wherever the package was installed.
    extra = ""
    env_file = os.environ.get("NOTIFY_ENV_FILE") or cfg.get("env_file")
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            path = Path(cfg["path"]).parent / path
        extra += f"EnvironmentFile={path}\n"
    if sched.get("user"):
        extra += f"User={sched['user']}\n"
    if sched.get("group"):
        extra += f"Group={sched['group']}\n"

    unit = f"notify-{cfg['name']}"
    return {
        f"{unit}.service": SERVICE.format(
            description=cfg["description"],
            python=sys.executable,
            config=cfg["path"],
            extra_lines=extra,
        ),
        f"{unit}.timer": TIMER.format(
            description=cfg["description"],
            on_calendar=sched["on_calendar"],
            persistent="true" if sched.get("persistent", True) else "false",
        ),
    }
