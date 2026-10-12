"""One hands-off cycle: check the news, publish well-sourced finds, send alerts.

    python -m scripts.watch                 # one cycle (what GitHub Actions runs on a schedule)
    python -m scripts.watch --ping https://your-site.onrender.com   # also wake the website

You normally don't run this yourself: the web app runs the same cycle every
WATCH_INTERVAL_MINUTES, and GitHub Actions runs it in production.

Finds that link to the LGU's own post, or that 2+ outlets report, are published
and alerted automatically. The rest stay pending and are listed in
ADMIN_DISCORD_WEBHOOK_URL for a person. See app/services/autopilot.py.
"""

import argparse
import logging
import sys
import time
import urllib.request

from app.services import autopilot, watcher


def ping(url: str) -> None:
    """Visit the site so a free host that sleeps when idle stays awake."""
    try:
        request = urllib.request.Request(url.rstrip("/") + "/health", headers={"User-Agent": watcher.USER_AGENT})
        with urllib.request.urlopen(request, timeout=90) as response:
            print(f"Pinged {url}: HTTP {response.status}")
    except Exception as exc:  # noqa: BLE001 - a failed ping must not fail the watch
        print(f"Ping failed: {exc}")


def run(ping_url: str | None) -> int:
    if ping_url:
        ping(ping_url)
    cycle = autopilot.run_cycle()
    report = cycle.watch

    for a in report.articles:
        when = f" [{a.date}]" if a.date else ""
        print(f"{a.status:10} {a.title[:70] or a.url}{when}")
        for c in a.items:
            flag = f"  ⚠ {' '.join(c.notes)}" if c.notes else ""
            if c.suspension_id in cycle.published:
                flag += f"  -> PUBLISHED ({cycle.published[c.suspension_id]})"
            elif c.suspension_id in cycle.held:
                flag += f"  -> needs review ({cycle.held[c.suspension_id]})"
            print(f"           {c.action:14} #{c.suspension_id} {c.location.name}: {c.line[:90]}{flag}")
        for line in a.unmatched:
            print(f"           unknown place  {line[:100]}")
        if a.error:
            print(f"           {a.error}")
    for error in report.errors:
        print(f"ERROR {error}")

    print(f"\n{len(report.new_items)} new, {len(report.new_proof)} new proof added, "
          f"{len(cycle.published)} published automatically, {len(cycle.held)} waiting for review.")
    if cycle.alerts:
        print(f"Alerts: {cycle.alerts.sent} sent, {cycle.alerts.failed} failed.")
    # Fail the run (red X in GitHub Actions) only if no source could be read at all.
    return 1 if report.errors and not report.articles else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", action="store_true", help="keep running")
    parser.add_argument("--every", type=int, default=15, help="minutes between runs with --loop (default 15)")
    parser.add_argument("--ping", metavar="URL", help="also visit this site to keep it awake")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.loop:
        sys.exit(run(args.ping))
    while True:
        run(args.ping)
        print(f"Next check in {args.every} minutes. Ctrl+C to stop.\n")
        time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
