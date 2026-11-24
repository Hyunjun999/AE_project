"""Read-only look at the Domeggook Open API. Nothing is ordered or changed.

Usage:
    python explore_dome.py search 강아지 발톱깎이             # items for a keyword
    python explore_dome.py search 무릎담요 --market supply --size 10
    python explore_dome.py item 12345678                      # full detail of one item

Raw responses are saved to exports/ so the real response format can be checked.
Needs DOMEGGOOK_API_KEY in backend/.env.
"""
import argparse
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
ENDPOINT = "https://www.domeggook.com/ssl/api/"
EXPORTS = HERE / "exports"


def call(params):
    key = os.getenv("DOMEGGOOK_API_KEY")
    if not key:
        raise SystemExit("DOMEGGOOK_API_KEY is missing in backend/.env")
    r = requests.get(ENDPOINT, params={"aid": key, "om": "json", **params}, timeout=30)
    print("status:", r.status_code)
    try:
        return r.json()
    except ValueError:
        print("The response is not JSON. First 500 characters:")
        print(r.text[:500])
        raise SystemExit(1)


def save(name, body):
    EXPORTS.mkdir(exist_ok=True)
    path = EXPORTS / name
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved to", path)


def outline(obj, depth=0, max_depth=2):
    """Print key names and value types, not the values."""
    if not isinstance(obj, dict):
        return
    for key, value in obj.items():
        if isinstance(value, dict):
            print("  " * depth + f"{key}: dict[{len(value)}]")
            if depth < max_depth:
                outline(value, depth + 1, max_depth)
        elif isinstance(value, list):
            print("  " * depth + f"{key}: list[{len(value)}]")
            if value and isinstance(value[0], dict) and depth < max_depth:
                outline(value[0], depth + 1, max_depth)
        else:
            print("  " * depth + f"{key}: {type(value).__name__}")


def find(obj, wanted):
    """Return the first dict, anywhere in the response, that has all the wanted keys."""
    if isinstance(obj, dict):
        if all(k in obj for k in wanted):
            return obj
        for value in obj.values():
            hit = find(value, wanted)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find(value, wanted)
            if hit is not None:
                return hit
    return None


def find_all(obj, wanted, out=None):
    """Return every dict, anywhere in the response, that has all the wanted keys."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        if all(k in obj for k in wanted):
            out.append(obj)
        else:
            for value in obj.values():
                find_all(value, wanted, out)
    elif isinstance(obj, list):
        for value in obj:
            find_all(value, wanted, out)
    return out


def dig(obj, path, default="-"):
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def cmd_search(args):
    keyword = " ".join(args.keyword)
    body = call({"ver": "4.1", "mode": "getItemList", "market": args.market,
                 "kw": keyword, "sz": args.size, "pg": 1})
    save(f"dome_search_{args.market}.json", body)
    print("Structure:")
    outline(body)

    header = find(body, ["numberOfItems"])
    if header:
        print(f"\n'{keyword}' on market '{args.market}': {header.get('numberOfItems')} items in total")
    items = find_all(body, ["no", "title", "price"])
    if not items:
        print("No items found in the response. Check the structure above for an error message.")
        return
    print(f"Showing {len(items)}:")
    print("no | price | MOQ | shipping (who/fee) | business only | title")
    for it in items:
        print(it.get("no"), "|", it.get("price"), "|", it.get("unitQty"), "|",
              f"{dig(it, 'deli.who')}/{dig(it, 'deli.fee')}", "|", it.get("comOnly"), "|", it.get("title"))


def cmd_item(args):
    body = call({"ver": "4.6", "mode": "getItemView", "no": args.no})
    save(f"dome_item_{args.no}.json", body)
    print("Structure:")
    outline(body, max_depth=1)

    item = find(body, ["basis"])
    if not item:
        print("No item found in the response. Check the structure above for an error message.")
        return
    print("\nFields the agent will use:")
    for label, path in [
        ("title", "basis.title"), ("status", "basis.status"), ("tax", "basis.tax"),
        ("price on Domeggook", "price.dome"), ("price on Domeme", "price.supply"),
        ("resale price rule", "price.resale"),
        ("stock", "qty.inventory"), ("MOQ on Domeggook", "qty.domeMoq"),
        ("shipping paid by", "deli.pay"), ("shipping on Domeggook", "deli.dome"),
        ("shipping on Domeme", "deli.supply"), ("average days to ship", "deli.sendAvg"),
        ("images may be reused", "desc.license.usable"), ("image licence note", "desc.license.msg"),
        ("origin", "detail.country"), ("maker", "detail.manufacturer"),
        ("safety certificate", "detail.safetyCert"),
        ("category", "category.current"), ("main image", "thumb.original"),
        ("seller type", "seller.type"), ("return shipping fee", "return.deliAmt"),
    ]:
        print(f"  {label:22}: {dig(item, path)}")


def main():
    parser = argparse.ArgumentParser(description="Read-only Domeggook Open API explorer")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="list items for a keyword")
    s.add_argument("keyword", nargs="+")
    s.add_argument("--market", choices=["dome", "supply"], default="dome",
                   help="dome = Domeggook (bulk), supply = Domeme (single units, drop shipping)")
    s.add_argument("--size", type=int, default=10, help="items to fetch (1-200)")
    s.set_defaults(func=cmd_search)

    i = sub.add_parser("item", help="full detail of one item")
    i.add_argument("no", type=int)
    i.set_defaults(func=cmd_item)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
