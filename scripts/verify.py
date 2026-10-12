"""Verify pending suspensions by id: make them public and send the alerts.

    python -m scripts.verify 12 15          # verify #12 and #15
    python -m scripts.verify --list         # show what's pending

This is the static-site version of PATCH /api/v1/suspensions/{id}/verify. On
GitHub it runs from Actions > Admin > Run workflow.
"""

import argparse
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, session_scope
from app.models import Status, Suspension
from app.services import autopilot, clock, notifications
from app.services import suspensions as suspension_service


def list_pending() -> None:
    with SessionLocal() as db:
        pending = db.scalars(
            select(Suspension).where(Suspension.status == Status.PENDING).order_by(Suspension.date.desc())
        ).all()
        if not pending:
            print("Nothing is pending.")
        for s in pending:
            why = autopilot.trust_reason(s) or "needs a person"
            print(f"#{s.id:<5} {s.date}  {s.location.name:30} {s.reason_detail or s.reason.value:35} [{why}]")
            for p in s.proof:
                print(f"         {p.kind.value:16} {p.url}")


def verify(ids: list[int]) -> int:
    settings, today = get_settings(), clock.today()
    failed = 0
    with SessionLocal() as db:
        for sid in ids:
            if db.get(Suspension, sid) is None:
                print(f"#{sid}: not found")
                failed += 1
                continue
            s, newly = suspension_service.verify_with_change(db, sid)
            if not newly:
                print(f"#{sid} {s.location.name}: already verified")
                continue
            report = notifications.send_all(notifications.plan(db, s, today, settings), session_scope, settings)
            print(f"#{sid} {s.location.name} {s.date}: verified, {report.sent} alert(s) sent")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", type=int, help="suspension ids to verify")
    parser.add_argument("--list", action="store_true", help="list pending suspensions")
    args = parser.parse_args()
    if args.list or not args.ids:
        list_pending()
        return
    sys.exit(verify(args.ids))


if __name__ == "__main__":
    main()
