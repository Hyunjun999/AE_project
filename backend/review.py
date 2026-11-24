"""Approval queue in the terminal (the web page does the same thing).

Usage:
    python review.py            # go through pending drafts one by one
    python review.py --list     # only show what is pending
Approving here does NOT publish anything; it only marks the draft as approved.
"""
import argparse
import getpass
import os

import db
import drafts


def show(v):
    print("-" * 72)
    print(f"Draft #{v['id']}  [{v['source']}]  supplier item: {v['supplier']}/{v['item_id']}")
    print(f"  Supplier name : {v['name']}")
    print(f"  Title         : {v['title']}  ({len(v['title'])} chars)")
    print(f"  Tags          : {', '.join(v['tags'])}")
    print(f"  Cost+shipping : {v['cost']} KRW | floor {v['floor']} | price {v['price']}")
    print(f"  Profit / sale : {v['profit']} KRW | margin {v['margin_pct']}%")
    if v["flags"]:
        print(f"  FLAGS         : {'; '.join(v['flags'])}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    con = db.connect()
    pending = [drafts.to_view(d) for d in db.drafts_by_status(con, "pending")]

    if args.list:
        for v in pending:
            show(v)
        c = db.draft_counts(con)
        print(f"\nPending: {c['pending']} | approved: {c['approved']} | rejected: {c['rejected']}")
        return

    reviewer = os.getenv("REVIEWER") or getpass.getuser()
    print(f"Reviewer: {reviewer} | pending drafts: {len(pending)}")
    for v in pending:
        show(v)
        while True:
            choice = input("  [a]pprove  [e]dit title  [r]eject  [s]kip  [q]uit > ").strip().lower()
            if choice == "s":
                break
            if choice == "q":
                return
            if choice == "a":
                ok, message = drafts.decide(con, v["id"], "approve", reviewer)
            elif choice == "e":
                ok, message = drafts.decide(con, v["id"], "approve", reviewer, input("  New title > "))
            elif choice == "r":
                ok, message = drafts.decide(con, v["id"], "reject", reviewer)
            else:
                continue
            print(" ", message)
            if ok:
                break
    print(f"\nDone. Approved so far: {db.draft_counts(con)['approved']}")


if __name__ == "__main__":
    main()
