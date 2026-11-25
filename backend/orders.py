"""Count orders toward the sales goal. Read-only: it asks Naver which orders changed and keeps a tally.

Only the order line id, its status and its dates are stored. Names, addresses and phone numbers are never requested.

Usage:
    python orders.py            # sync now and print the tally
Set ORDERS_SINCE=2026-11-10 in backend/.env to choose the first day that counts.
"""
import datetime as dt
import os
import time

import db

KST = dt.timezone(dt.timedelta(hours=9))
SYNC_EVERY = 300                      # seconds between syncs
NOT_A_SALE = {"PAYMENT_WAITING", "CANCELED", "RETURNED", "CANCELED_BY_NOPAYMENT"}
PATH = "/v1/pay-order/seller/product-orders/last-changed-statuses"
_state = {"at": 0.0, "error": None}


def first_day():
    """The first day that counts. From ORDERS_SINCE, or 14 days before the first sync."""
    text = os.getenv("ORDERS_SINCE", "").strip()
    if text:
        return dt.datetime.fromisoformat(text).replace(tzinfo=KST)
    return (dt.datetime.now(KST) - dt.timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)


def changed_rows(payload):
    """Every dict in the response that describes one order line, wherever it sits."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "productOrderId" in node:
                found.append(node)
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def sync(con, force=False):
    """Ask Naver for order changes since the last sync, one day at a time. Never raises."""
    if not force and time.time() - _state["at"] < SYNC_EVERY:
        return
    _state["at"] = time.time()
    try:
        from naver import api

        saved = db.get_setting(con, "orders_synced_to")
        start = dt.datetime.fromisoformat(saved) if saved else first_day()
        if not db.get_setting(con, "orders_since"):
            db.set_setting(con, "orders_since", start.isoformat(timespec="seconds"))
        now = dt.datetime.now(KST)
        cursor = start - dt.timedelta(minutes=10)   # small overlap so nothing falls between two syncs
        for _ in range(120):                         # at most 120 days in one go
            if cursor >= now:
                break
            r = api("GET", PATH, params={"lastChangedFrom": cursor.isoformat(timespec="milliseconds")})
            if r.status_code != 200:
                _state["error"] = f"Commerce API returned {r.status_code} for orders"
                con.commit()
                return
            for row in changed_rows(r.json()):
                db.upsert_order(con, str(row["productOrderId"]), row.get("productOrderStatus"),
                                row.get("paymentDate"), row.get("lastChangedDate"))
            con.commit()                             # never hold the write lock across the next call and pause
            cursor += dt.timedelta(hours=24)        # each call covers the 24 hours after lastChangedFrom
            time.sleep(0.6)                          # the API allows 2 requests per second
        con.commit()
        db.set_setting(con, "orders_synced_to", now.isoformat(timespec="seconds"))
        _state["error"] = None
    except Exception as e:  # no network, missing keys, IP not allowed, ...
        _state["error"] = f"orders unavailable: {e!r}"


def tally(con, days=14):
    """{'count', 'by_day': [{'date', 'n'}...], 'since', 'error'} from what has been stored."""
    sold = [o for o in db.all_orders(con) if (o["status"] or "") not in NOT_A_SALE]
    per_day = {}
    for o in sold:
        day = (o["paid_at"] or o["last_changed"] or "")[:10]
        per_day[day] = per_day.get(day, 0) + 1
    today = dt.datetime.now(KST).date()
    by_day = [{"date": d.isoformat(), "n": per_day.get(d.isoformat(), 0)}
              for d in (today - dt.timedelta(days=i) for i in range(days - 1, -1, -1))]
    return {"count": len(sold), "by_day": by_day,
            "since": (db.get_setting(con, "orders_since") or "")[:10], "error": _state["error"]}


if __name__ == "__main__":
    connection = db.connect()
    sync(connection, force=True)
    result = tally(connection)
    if result["error"]:
        print("Problem:", result["error"])
    print(f"Orders counted since {result['since']}: {result['count']}")
    print("Last 14 days:", " ".join(f"{d['date'][5:]}={d['n']}" for d in result["by_day"]))
    print("Statuses stored:", sorted({o["status"] or "-" for o in db.all_orders(connection)}) or "none yet")
