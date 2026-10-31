"""Load the national holidays (app/data/holidays/<year>.json) as nationwide suspensions.

    python -m scripts.holidays

Safe to run every time: holidays already in the database are skipped. The
"Update site" workflow runs it on every cycle.
"""

import os
import sys

from app.db import SessionLocal
from app.services import clock, holidays


def main() -> int:
    loaded = holidays.load_all()
    with SessionLocal() as db:
        report = holidays.sync(db, loaded)
    for h in report.created:
        print(f"added    {h.date}  {h.detail}")
    print(f"Holidays: {len(report.created)} added, {report.already_listed} already listed, "
          f"{report.working_days_skipped} working day(s) skipped.")

    missing = holidays.missing_next_year(clock.today())
    if missing:
        message = (f"No holiday file for {missing} yet. When the {missing} holidays proclamation is out, "
                   f"add app/data/holidays/{missing}.json.")
        # Shows as a yellow warning on the GitHub Actions run.
        print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else f"WARNING: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
