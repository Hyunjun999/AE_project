"""Create drafts from candidates.csv (command line).

Usage:
    python draft.py --no-llm     # formula titles only, 0 tokens
    python draft.py              # LLM titles in small batches, within the weekly token cap
Nothing is published to the store by this script.
"""
import argparse

import db
import drafts
import llm

LABELS = {"gate0_reject": "Gate 0 REJECT ", "skip": "Skip          ", "draft": "Draft         ",
          "notice": "Notice        ", "error": "Error         "}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(drafts.CANDIDATES_CSV))
    parser.add_argument("--no-llm", action="store_true", help="use formula titles only (0 tokens)")
    args = parser.parse_args()

    con = db.connect()
    result = drafts.generate(con, args.csv, use_llm=not args.no_llm)
    for e in result["events"]:
        print(f"{LABELS[e['kind']]} {e['item_id']}: {e['message']}" if e["item_id"] else f"{LABELS[e['kind']]} {e['message']}")
    print(f"\nPending drafts: {db.draft_counts(con)['pending']} | "
          f"tokens used this week: {llm.used_this_week(con)} of {llm.WEEKLY_CAP}")
    if any(e["kind"] == "error" for e in result["events"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
