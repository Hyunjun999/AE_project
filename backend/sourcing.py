"""Find products to sell on Domeggook, let the agent pick, and queue the picks for a person.

Steps (only step 3 spends LLM tokens):
  1. search Domeggook for a keyword                          (Open API, no tokens)
  2. drop what the rules forbid: business-only or adult items, large minimum orders,
     images we may not reuse, items no longer on sale        (no tokens)
  3. the agent picks a few of what is left and writes a title and tags for each  (one LLM call)
  4. each pick becomes a pending draft. A person sets the price and approves it.
Nothing here orders from the supplier or lists anything in the store.

Usage:
    python sourcing.py 강아지 발톱깎이
    python sourcing.py 무릎담요 --market supply --no-llm
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

import db
import drafts
import llm
import store
from price import price_floor

load_dotenv(Path(__file__).with_name(".env"))
ENDPOINT = "https://www.domeggook.com/ssl/api/"
MARKETS = {"dome": "Domeggook", "supply": "Domeme"}
MAX_MOQ = int(os.getenv("SOURCING_MAX_MOQ", "10"))   # drop items whose minimum order is larger than this
PAUSE = 0.3                                          # seconds between Domeggook calls

SYSTEM = (
    "You choose products for a small Korean Naver SmartStore that must sell 100 orders in a month "
    "with almost no ad budget. Each line is: number | supplier title | cost with shipping | lowest price we may charge. "
    "Pick the items most likely to sell at or above their lowest price: a clear everyday use, a low price point, "
    "easy to explain in one photo. Do not pick two near-identical items. "
    "For each pick write a Korean product title of at most 45 characters that contains the search keyword and "
    "makes no superlative or unverifiable claim, and three Korean search tags. "
    "Supplier titles often carry another shop's name or a promotion in brackets, such as [이더블유] or (당일출고): "
    "never copy those, and never put a brand or shop name in a title or tag. "
    "Also write a short Korean product introduction for the product page: three lines, each under 40 characters, "
    "separated by \\n. Say what the product is, who or when it is for, and one feature. Use only what the supplier "
    "title says: no material, size, number, certification, origin or effect that the title does not state, and no "
    "superlatives. "
    'Answer with JSON only: [{"no": 123, "reason": "under 15 English words", "title": "...", "tags": ["..", "..", ".."], '
    '"description": "line 1\\nline 2\\nline 3"}]'
)


class SourcingError(Exception):
    pass


# ---------------------------------------------------------------- Domeggook client
def truthy(value):
    return value is True or str(value).strip().lower() in ("true", "1", "y", "yes")


def number(value, default=0):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def unit_price(value):
    """Domeggook prices are a number, or tiers like "1+3800|20+3500". Return the price of the smallest tier."""
    text = str(value or "")
    if "+" not in text:
        return number(text)
    tiers = [part.split("+") for part in text.split("|") if "+" in part]
    return number(min(tiers, key=lambda t: number(t[0], 10**9))[1])


def call(params):
    key = os.getenv("DOMEGGOOK_API_KEY")
    if not key:
        raise SourcingError("DOMEGGOOK_API_KEY is missing in backend/.env")
    try:
        r = requests.get(ENDPOINT, params={"aid": key, "om": "json", **params}, timeout=30)
        body = r.json()
    except (requests.RequestException, ValueError) as e:   # the error text quotes the address, key included
        raise SourcingError(f"Domeggook did not answer ({type(e).__name__})")
    if not isinstance(body, dict) or "domeggook" not in body:
        raise SourcingError(f"Domeggook returned an unexpected answer: {json.dumps(body, ensure_ascii=False)[:200]}")
    return body["domeggook"]


def search(keyword, market="dome", size=10, max_moq=None):
    params = {"ver": "4.1", "mode": "getItemList", "market": market, "kw": keyword, "sz": size, "pg": 1}
    if max_moq:
        params.update(mnq=1, mxq=max_moq)   # ask Domeggook for items whose minimum order fits
    data = call(params)
    items = (data.get("list") or {}).get("item") or []
    if isinstance(items, dict):   # a single result comes back as one object
        items = [items]
    total = number((data.get("header") or {}).get("numberOfItems"))
    return total, [normalise(item) for item in items]


def normalise(item):
    deli = item.get("deli") or {}
    who = str(deli.get("who") or "")
    shipping = 0 if who == "S" else number(deli.get("fee"))
    cost = unit_price(item.get("price"))
    return {
        "no": number(item.get("no")), "name": str(item.get("title") or "").strip(),
        "cost": cost, "shipping": shipping, "landed": cost + shipping, "floor": price_floor(cost + shipping),
        "moq": max(1, number(item.get("unitQty"), 1)),
        "business_only": truthy(item.get("comOnly")), "adult_only": truthy(item.get("adultOnly")),
        "thumb": item.get("thumb") or "", "url": item.get("url") or "",
    }


def detail(no):
    data = call({"ver": "4.6", "mode": "getItemView", "no": no})

    def dig(path, default=None):
        node = data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    resale = dig("price.resale") or {}
    return {
        "on_sale": dig("basis.status") == "판매중",
        "images_usable": truthy(dig("desc.license.usable")),
        "deny_market": truthy(dig("etc.denyMarket")),   # the supplier forbids resale on open markets
        "origin": dig("detail.country") or "", "seller_type": dig("seller.type") or "",
        "resale_min": number(resale.get("minumum") or resale.get("minimum")) if isinstance(resale, dict) else 0,
        "certified": bool(dig("detail.safetyCert")),
        "category": (dig("category.current") or {}).get("name", "") if isinstance(dig("category.current"), dict) else "",
        "image": dig("thumb.original") or "",
    }


# ---------------------------------------------------------------- rules and picking
CARRIED = ("SALE", "HIDDEN", "OUTOFSTOCK")   # store statuses that mean "we already carry this"
squash = lambda text: re.sub(r"\s+", "", text or "").lower()


def store_products():
    """The store's products as [{'no', 'name', 'status', 'code', ...}]; empty when the store cannot be read."""
    shop = store.snapshot()
    return [] if "error" in shop else shop["products"]


def already_carried(keyword, products):
    """Products we already carry whose name contains the search keyword (ignoring spaces)."""
    return [p for p in products if p["status"] in CARRIED and squash(keyword) in squash(p["name"])]


def first_check(item, con, supplier, max_moq=MAX_MOQ, listed_codes=None):
    """Reasons to drop an item using only the search result. Empty string means it may continue."""
    in_store = (listed_codes or {}).get(f"G3-{item['no']}")
    if in_store:
        return f"already in the store as product {in_store}"
    if item["business_only"]:
        return "only businesses may buy it"
    if item["adult_only"]:
        return "adults-only item"
    if item["cost"] <= 0:
        return "no usable price"
    if item["moq"] > max_moq:
        return f"minimum order is {item['moq']} units (limit {max_moq})"
    row = con.execute("SELECT id FROM candidates WHERE supplier = ? AND item_id = ?", (supplier, str(item["no"]))).fetchone()
    if row and db.has_open_draft(con, row["id"]):
        return "already drafted"
    return ""


def notes_for(item, info):
    notes = []
    if item["moq"] > 1:
        notes.append(f"minimum order {item['moq']} units")
    if info["seller_type"] in ("간이과세자", "개인판매자"):
        notes.append(f"seller is {info['seller_type']}: may not issue tax invoices")
    if info["resale_min"]:
        notes.append(f"supplier's minimum resale price {info['resale_min']} KRW")
    if info["origin"]:
        notes.append(f"origin: {info['origin']}")
    if not info["certified"]:
        notes.append("no safety certificate on the supplier page")
    return notes


BRACKETED = re.compile(r"\[([^\]]*)\]|【([^】]*)】")   # usually another shop's name or a promotion
QUANTITY = re.compile(r"\d+\s*(개|입|매|장|세트|팩|켤레|묶음|족|종|구|[Pp](?:cs)?|EA|ea)|\d\s*\+\s*\d")   # but [2개입] or [1+1] describes the product


def clean_title(text):
    """Drop bracketed shop names and promotions (keeping pack sizes), squeeze spaces, cut to the title limit."""
    squeeze = lambda s: re.sub(r"\s+", " ", s).strip()
    kept = BRACKETED.sub(lambda m: " " + (m.group(1) or m.group(2) or "") + " "
                         if QUANTITY.search(m.group(1) or m.group(2) or "") else " ", text)
    return (squeeze(kept) or squeeze(text))[:drafts.MAX_TITLE].strip()


def formula_picks(pool, keyword, how_many):
    """No LLM: take the cheapest items and build titles from the keyword and the supplier's title."""
    picks = []
    for item in sorted(pool, key=lambda x: x["landed"])[:how_many]:
        base = clean_title(item["name"])   # clean first, so a keyword that only sat in a shop name is added back
        title = base if keyword.replace(" ", "") in base.replace(" ", "") else f"{keyword} {base}"
        picks.append({"no": item["no"], "reason": "Lowest cost among the items that passed the rules.",
                      "title": clean_title(title), "tags": [keyword], "source": "formula"})
    return picks

def llm_picks(con, pool, keyword, how_many, carried=()):
    lines = "\n".join(f"{x['no']} | {x['name'][:40]} | {x['landed']} | {x['floor']}" for x in pool)
    store_lines = "\n".join(f"- {p['name'][:40]}" for p in carried[:15])
    known = (f"The store already sells the products below. Do not pick an item that is the same kind of product "
             f"as one of them; answer [] if every candidate is.\n{store_lines}\n" if store_lines else "")
    reply = llm.chat(con, [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Search keyword: {keyword}\nPick at most {how_many}.\n{known}Candidates:\n{lines}"},
    ], purpose="sourcing", max_tokens=260 * how_many + 60)
    start, end = reply.find("["), reply.rfind("]")
    rows = json.loads(reply[start:end + 1])
    known = {x["no"] for x in pool}
    picks = []
    for row in rows:
        no = number(row.get("no"), -1)
        if no in known and row.get("title") and no not in {p["no"] for p in picks}:
            description = "\n".join(line.strip() for line in str(row.get("description") or "").splitlines() if line.strip())
            if drafts.check_description(description):   # a banned claim or too long: leave it for a person to write
                description = ""
            picks.append({"no": no, "reason": str(row.get("reason") or "")[:160], "title": clean_title(str(row["title"])),
                          "tags": [str(t).strip() for t in (row.get("tags") or []) if str(t).strip()][:5], "source": "llm",
                          "description": description})
    return picks[:how_many]


# ---------------------------------------------------------------- one run
def run(con, keyword, market="dome", look=10, picks=3, use_llm=True, by=None, max_moq=None):
    """Search, filter, pick and queue drafts. Returns {'events', 'created', 'items', 'total', ...}. Never raises."""
    keyword = " ".join(keyword.split())
    events, items = [], []
    result = {"events": events, "items": items, "created": 0, "total": 0, "looked": 0, "used_llm": False}
    supplier = "domeggook" if market == "dome" else "domeme"

    def stop(message):
        events.append({"kind": "error", "message": message})
        return result

    if not keyword:
        return stop("Type a keyword to search for.")
    if market not in MARKETS:
        return stop(f"Unknown market '{market}'.")
    look, picks = max(1, min(int(look), 30)), max(1, min(int(picks), 5))
    max_moq = MAX_MOQ if max_moq in (None, "") else max(1, min(int(max_moq), 50))

    # What the store already carries: searching for it again would only draft a second copy.
    listed = store_products()
    carried = [p for p in listed if p["status"] in CARRIED]
    same = already_carried(keyword, listed)
    if same:
        names = "; ".join(f"{p['name'][:40]} (product {p['no']})" for p in same[:3])
        return stop(f"The store already sells {keyword}: {names}. Nothing was searched; "
                    "try something the store does not carry yet.")
    listed_codes = {p["code"]: p["no"] for p in listed if p.get("code")}

    try:
        result["total"], found = search(keyword, market, look, max_moq)
    except SourcingError as e:
        return stop(str(e))
    result["looked"] = len(found)
    if not found:
        return stop(f"Domeggook has no items for '{keyword}'.")

    pool = []
    for item in found:
        view = {k: item[k] for k in ("no", "name", "cost", "shipping", "landed", "floor", "moq", "thumb", "url")}
        items.append(view)
        why = first_check(item, con, supplier, max_moq, listed_codes)
        if not why:
            try:
                time.sleep(PAUSE)
                info = detail(item["no"])
            except SourcingError as e:
                why = f"details could not be read ({e})"
            else:
                if not info["on_sale"]:
                    why = "no longer on sale"
                elif not info["images_usable"]:
                    why = "the supplier does not allow its images to be reused"
                elif info["deny_market"]:
                    why = "the supplier does not allow resale on open markets"
        if why:
            view.update(outcome="dropped", why=why)
        else:
            item["info"] = info
            view.update(outcome="passed", why="Passed the rules; not picked this time.")
            pool.append(item)
    if not pool:
        events.append({"kind": "notice", "message": "Every item was dropped by the rules, so nothing was drafted."})
        db.add_run(con, keyword, market, {k: result[k] for k in ("items", "created", "total", "looked", "used_llm")})
        return result

    # With the agent asked, only its picks become drafts: if it picks nothing or cannot answer, nothing is drafted.
    # The cheapest items are taken only when asked for ("Pick the cheapest" / --no-llm).
    if use_llm:
        chosen = []
        try:
            chosen = llm_picks(con, pool, keyword, picks, carried)
            result["used_llm"] = True
            if not chosen:
                events.append({"kind": "notice", "message": "The agent picked nothing: every candidate was weak or like "
                                                            "something the store already sells. Nothing was drafted."})
        except llm.TokenCapReached as e:
            events.append({"kind": "error", "message": f"Weekly token cap reached, so nothing was drafted. ({e})"})
        except Exception as e:  # network trouble, an empty answer, or one that is not JSON
            events.append({"kind": "error", "message": f"The agent could not answer ({e!r}), so nothing was drafted. "
                                                       "Use 'Pick the cheapest' to draft without the agent."})
    else:
        chosen = formula_picks(pool, keyword, picks)

    by_no = {x["no"]: x for x in pool}
    views = {v["no"]: v for v in items}
    for pick in chosen:
        item = by_no[pick["no"]]
        info = item["info"]
        extra = {"thumb": info["image"] or item["thumb"], "url": item["url"], "moq": item["moq"],
                 "resale_min": info["resale_min"], "market": market, "notes": notes_for(item, info)}
        tags = pick["tags"] or [keyword]
        candidate_id = db.upsert_candidate(con, {
            "supplier": supplier, "item_id": str(item["no"]), "name": item["name"], "cost": item["cost"],
            "shipping": item["shipping"], "keyword1": keyword,
            "keyword2": tags[0] if tags else None, "keyword3": tags[1] if len(tags) > 1 else None,
            "extra": json.dumps(extra, ensure_ascii=False)})
        flags = drafts.check_title(pick["title"], {"keyword1": keyword, "name": item["name"]})
        start_price = max(item["floor"], info["resale_min"])
        db.add_draft(con, candidate_id, pick["title"], tags, start_price, item["floor"], pick["source"],
                     "; ".join(flags), pick["reason"], pick.get("description") or None)
        views[item["no"]].update(outcome="picked", why=pick["reason"], title=pick["title"])
        result["created"] += 1

    db.log_action(con, "sourcing_run", f"keyword={keyword} market={market} looked={result['looked']} "
                                       f"picked={result['created']} llm={result['used_llm']}" + (f" by={by}" if by else ""))
    db.add_run(con, keyword, market, {k: result[k] for k in ("items", "created", "total", "looked", "used_llm")})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find products on Domeggook and queue the agent's picks as drafts")
    parser.add_argument("keyword", nargs="+")
    parser.add_argument("--market", choices=list(MARKETS), default="dome")
    parser.add_argument("--look", type=int, default=10, help="how many search results to look at (1-30)")
    parser.add_argument("--picks", type=int, default=3, help="how many to draft (1-5)")
    parser.add_argument("--no-llm", action="store_true", help="pick the cheapest instead of asking the agent")
    parser.add_argument("--max-moq", type=int, help=f"drop items whose minimum order is above this (default {MAX_MOQ})")
    args = parser.parse_args()

    connection = db.connect()
    outcome = run(connection, " ".join(args.keyword), args.market, args.look, args.picks, use_llm=not args.no_llm,
                  by="cli", max_moq=args.max_moq)
    for event in outcome["events"]:
        print(f"{event['kind'].upper()}: {event['message']}")
    print(f"Domeggook has {outcome['total']} items; looked at {outcome['looked']}; drafted {outcome['created']}.")
    for row in outcome["items"]:
        print(f"  {row.get('outcome', '-'):8} {row['no']} | cost {row['cost']}+{row['shipping']} floor {row['floor']} | "
              f"{row['name'][:36]} | {row.get('why', '')}")
    print(f"Tokens used this week: {llm.used_this_week(connection)} of {llm.WEEKLY_CAP}")
