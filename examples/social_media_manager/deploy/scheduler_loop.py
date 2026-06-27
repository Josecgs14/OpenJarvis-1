#!/usr/bin/env python3
"""Self-contained 24/7 scheduler for the social media manager.

A tiny, dependency-free loop meant to run forever on a server (inside
Docker, a VPS, or any always-on box). It calls the agent's own CLI on a
schedule, so there's no cron to configure — just keep this process alive
(``docker compose up -d`` or a systemd service).

Schedule (all times in the container/host local timezone):
  * every ``RUN_EVERY_MIN`` minutes  ->  ``run``   (publish anything due)
  * Mondays at ``PLAN_HOUR``:00       ->  ``plan``  (fill next week)
  * Sundays at ``REPORT_HOUR``:00     ->  ``report`` (weekly evaluation)

Everything is configurable via environment variables so you never edit
code to retune it:

  RUN_EVERY_MIN   default 30
  PLAN_HOUR       default 8     PLAN_DOW    default 0 (Mon; 0=Mon..6=Sun)
  REPORT_HOUR     default 9     REPORT_DOW  default 6 (Sun)
  PLAN_DAYS       default 7
  AGENT_ARGS      extra args passed to every command, e.g. "--engine cloud"
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_AGENT = Path(__file__).resolve().parent.parent / "social_media_manager.py"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _run(*args: str) -> None:
    """Invoke the agent CLI once, streaming its output, never raising."""
    extra = shlex.split(os.environ.get("AGENT_ARGS", ""))
    cmd = [sys.executable, str(_AGENT), *extra, *args]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] $ {' '.join(args)} (+{len(extra)} global args)", flush=True)
    try:
        subprocess.run(cmd, check=False)
    except Exception as exc:  # never let one failure kill the loop
        print(f"[{stamp}] command errored: {exc}", flush=True)


def main() -> None:
    run_every = max(1, _int_env("RUN_EVERY_MIN", 30))
    plan_hour = _int_env("PLAN_HOUR", 8)
    plan_dow = _int_env("PLAN_DOW", 0)
    report_hour = _int_env("REPORT_HOUR", 9)
    report_dow = _int_env("REPORT_DOW", 6)
    plan_days = _int_env("PLAN_DAYS", 7)

    print(
        "social media manager — 24/7 scheduler started\n"
        f"  run     every {run_every} min\n"
        f"  plan    {plan_days}d, dow={plan_dow} at {plan_hour}:00\n"
        f"  report  dow={report_dow} at {report_hour}:00",
        flush=True,
    )

    # Remember the last hour we fired the weekly jobs so they run once.
    last_plan_key = last_report_key = ""
    # Fire a publish pass immediately on boot, then every run_every minutes.
    next_run = 0.0

    while True:
        now = datetime.now()
        if time.time() >= next_run:
            _run("run")
            next_run = time.time() + run_every * 60

        hour_key = now.strftime("%Y-%m-%d-%H")
        if now.weekday() == plan_dow and now.hour == plan_hour:
            if hour_key != last_plan_key:
                _run("plan", "--days", str(plan_days))
                last_plan_key = hour_key
        if now.weekday() == report_dow and now.hour == report_hour:
            if hour_key != last_report_key:
                _run("report", "--days", "7")
                last_report_key = hour_key

        time.sleep(30)


if __name__ == "__main__":
    main()
