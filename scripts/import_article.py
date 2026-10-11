"""Import suspensions from one article or announcement.

    # A news article or LGU website (date read from the headline):
    python -m scripts.import_article https://newsinfo.inquirer.net/2301928/...

    # An announcement written as a paragraph, not a list: say which place it covers
    python -m scripts.import_article https://quezoncity.gov.ph/... --place metro-manila --kind official_website

    # A Facebook post (Facebook can't be fetched): copy its text into a file
    python -m scripts.import_article https://www.facebook.com/.../posts/... --file post.txt --date 2026-10-12 --source-name "Angeles City Information Office"

Shows what it found and saves nothing, unless you add --save (as pending) and
optionally --verify (public right away, and sends alerts).
"""

import argparse
import datetime as dt
import sys
import urllib.parse

from app.config import get_settings
from app.db import SessionLocal, session_scope
from app.models import Reason, SourceKind
from app.services import clock, importer, notifications, watcher
from app.services import suspensions as suspension_service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="where the announcement is; saved as proof")
    parser.add_argument("--file", help="read the text from this file instead of fetching the URL")
    parser.add_argument("--date", type=dt.date.fromisoformat, help="suspended day, YYYY-MM-DD (default: from the headline)")
    parser.add_argument("--source-name", help="who published it (default: the website's name)")
    parser.add_argument("--kind", choices=[k.value for k in SourceKind], help="default: guessed from the URL")
    parser.add_argument("--reason", choices=[r.value for r in Reason], help="default: guessed from the headline")
    parser.add_argument("--reason-detail", help='e.g. "Southwest monsoon (habagat)"')
    parser.add_argument("--place", help="location slug: read the whole text as one suspension for this place "
                        "(for announcements written as a paragraph)")
    parser.add_argument("--save", action="store_true", help="save as pending suspensions")
    parser.add_argument("--verify", action="store_true", help="with --save: verify them and send alerts")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            raw = f.read()
        page = importer.html_to_page(raw) if "<" in raw and ">" in raw else importer.Page("", raw, [])
    else:
        # On sites the watcher knows, read only the article body.
        host = urllib.parse.urlsplit(args.url).hostname or ""
        known = next((s for s in watcher.SOURCES if urllib.parse.urlsplit(s.index_url).hostname == host), None)
        try:
            page = importer.html_to_page(watcher.PoliteFetcher()(args.url), known.content_id if known else None)
        except watcher.FetchError as exc:
            sys.exit(f"Couldn't fetch it ({exc}). Copy the text into a file and use --file.")

    today = clock.today()
    day = args.date or watcher.headline_date(page, today)
    if day is None:
        sys.exit("Couldn't read the date from the headline. Add --date YYYY-MM-DD.")
    name = args.source_name or urllib.parse.urlsplit(args.url).hostname.removeprefix("www.")
    reason = Reason(args.reason) if args.reason else importer.parse_reason(page.title, Reason.WEATHER)

    with SessionLocal() as db:
        mode = importer.article_mode(page.title, "\n".join(page.text.splitlines()[:30]))
        index = importer.PlaceIndex.from_db(db)
        if args.place:
            place = index.lookup(args.place) or next((l for ls in index.by_key.values() for l in ls if l.slug == args.place), None)
            if place is None:
                sys.exit(f"Unknown place '{args.place}'. Find slugs with GET /api/v1/locations?search=...")
            result = importer.parse_whole(place, page.text, day, reason, mode)
        else:
            result = importer.parse_text(index, page.text, day, reason, mode)
        if args.save:
            importer.import_parsed(db, result, day, importer.SourceInfo(
                url=args.url, name=name, kind=SourceKind(args.kind) if args.kind else None,
                reason_detail=args.reason_detail,
            ))

        print(f"{page.title or args.url}\nDate: {day:%A, %B %d, %Y}   Source: {name}\n")
        for c in result.candidates:
            scope = ", ".join(level.value for level in c.levels)
            extra = [c.school_type.value, c.mode.value, c.time_scope.value]
            if c.until_lifted:
                extra.append(f"until lifted, classes resume {c.lifted_on}")
            action = f"{c.action} #{c.suspension_id}" if c.action else "found"
            print(f"  {action:20} {c.location.name}: {scope}; {'; '.join(extra)}")
            print(f"  {'':20} \"{c.line[:110]}\"")
            for note in c.notes:
                print(f"  {'':20} ⚠ {note}")
        for line in result.unmatched:
            print(f"  {'unknown place':20} {line[:110]}")
        if not result.candidates:
            print("  Nothing found. Is this a list of suspensions?")

        if args.save and args.verify:
            settings = get_settings()
            for c in result.candidates:
                if c.action != "created":
                    continue
                suspension, newly = suspension_service.verify_with_change(db, c.suspension_id)
                if newly:
                    notifications.send_all(notifications.plan(db, suspension, today, settings), session_scope, settings)
            print("\nVerified and alerts sent.")
        elif args.save:
            print("\nSaved as pending. Verify with: PATCH /api/v1/suspensions/{id}/verify")
        else:
            print("\nNothing saved (add --save).")


if __name__ == "__main__":
    main()
