"""Check suspension news pages and import anything new as pending.

    python -m scripts.watch                 # one run (what GitHub Actions does)
    python -m scripts.watch --loop          # keep running, every 15 minutes (for your laptop)
    python -m scripts.watch --ping https://your-site.onrender.com   # also wake the website

New suspensions are saved as *pending* and reported to ADMIN_DISCORD_WEBHOOK_URL.
Nothing is public until someone verifies it.
"""

import argparse
import logging
import sys
import time
import urllib.request

from app.db import SessionLocal
from app.services import watcher


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
    with SessionLocal() as db:
        report = watcher.run_once(db, watcher.PoliteFetcher())

    for a in report.articles:
        when = f" [{a.date}]" if a.date else ""
        print(f"{a.status:10} {a.title[:70] or a.url}{when}")
        for c in a.items:
            flag = f"  ⚠ {' '.join(c.notes)}" if c.notes else ""
            print(f"           {c.action:14} #{c.suspension_id} {c.location.name}: {c.line[:90]}{flag}")
        for line in a.unmatched:
            print(f"           unknown place  {line[:100]}")
        if a.error:
            print(f"           {a.error}")
    for error in report.errors:
        print(f"ERROR {error}")

    print(f"\n{len(report.new_items)} new pending, {len(report.new_proof)} new proof added.")
    if watcher.notify_admin(report):
        print("Reported to the admin Discord channel.")
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
