"""Read-only look at the store through the Commerce API. Nothing here changes the store.

Usage:
    python explore_naver.py account
    python explore_naver.py product 13733764452      # saves the full JSON to exports/
    python explore_naver.py categories 발톱깎이
    python explore_naver.py orders 24                # orders changed in the last 24 hours
    python explore_naver.py tags 강아지 발톱깎이
"""
import argparse
import datetime as dt
import json
from pathlib import Path

from naver import api

EXPORTS = Path(__file__).parent / "exports"


def outline(value, depth=0, max_depth=2):
    """Print the shape of a JSON value (keys and types) without printing the data itself."""
    pad = "  " * depth
    if isinstance(value, dict):
        for k, v in value.items():
            kind = type(v).__name__ + (f"[{len(v)}]" if isinstance(v, (list, dict)) else "")
            print(f"{pad}{k}: {kind}")
            if depth < max_depth and isinstance(v, (dict, list)):
                outline(v, depth + 1, max_depth)
    elif isinstance(value, list) and value:
        outline(value[0], depth, max_depth)


def fail(r):
    print("status:", r.status_code)
    print("Request failed:", r.text[:800])
    raise SystemExit(1)


def cmd_account(_):
    r = api("GET", "/v1/seller/account")
    if r.status_code != 200:
        fail(r)
    print("status: 200. Fields returned (values hidden):")
    outline(r.json())


def cmd_product(args):
    r = api("GET", f"/v2/products/origin-products/{args.origin_product_no}")
    if r.status_code != 200:
        fail(r)
    EXPORTS.mkdir(exist_ok=True)
    path = EXPORTS / f"product_{args.origin_product_no}.json"
    path.write_text(json.dumps(r.json(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("status: 200. Saved to", path)
    print("Structure (this is the template for creating products):")
    outline(r.json(), max_depth=1)


def cmd_categories(args):
    r = api("GET", "/v1/categories")
    if r.status_code != 200:
        fail(r)
    data = r.json()
    rows = data if isinstance(data, list) else data.get("contents", [])
    hits = [c for c in rows if args.keyword in json.dumps(c, ensure_ascii=False)]
    print(f"status: 200. {len(rows)} categories in total, {len(hits)} contain '{args.keyword}':")
    for c in hits[:30]:
        print(" ", c.get("id"), "|", c.get("wholeCategoryName") or c.get("name"), "| leaf:", c.get("last"))
    if not rows:
        print("Unexpected response shape:")
        outline(data)


def cmd_orders(args):
    since = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))) - dt.timedelta(hours=args.hours)
    r = api("GET", "/v1/pay-order/seller/product-orders/last-changed-statuses",
            params={"lastChangedFrom": since.isoformat(timespec="milliseconds")})
    if r.status_code != 200:
        fail(r)
    data = r.json()
    rows = (data.get("data") or {}).get("lastChangeStatuses", [])
    print(f"status: 200. {len(rows)} order changes in the last {args.hours} hours.")
    for o in rows:  # status only; no buyer details are printed
        print(" ", o.get("productOrderId"), o.get("productOrderStatus"), o.get("lastChangedType"),
              o.get("lastChangedDate"))
    if not rows:
        print("Response keys:", list(data.keys()))


def cmd_tags(args):
    r = api("GET", "/v2/tags/recommend-tags", params={"keyword": " ".join(args.keyword)})
    if r.status_code != 200:
        fail(r)
    data = r.json()
    rows = data if isinstance(data, list) else data.get("contents", data)
    print("status: 200.")
    if isinstance(rows, list):
        print("Recommended tags:", ", ".join(str(t.get("text", t)) if isinstance(t, dict) else str(t) for t in rows))
    else:
        outline(rows)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("account").set_defaults(func=cmd_account)
    p = sub.add_parser("product"); p.add_argument("origin_product_no"); p.set_defaults(func=cmd_product)
    p = sub.add_parser("categories"); p.add_argument("keyword"); p.set_defaults(func=cmd_categories)
    p = sub.add_parser("orders"); p.add_argument("hours", type=int, nargs="?", default=24); p.set_defaults(func=cmd_orders)
    p = sub.add_parser("tags"); p.add_argument("keyword", nargs="+"); p.set_defaults(func=cmd_tags)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()