"""Register an approved draft as a product in the Naver smart store.

Nothing here runs unless a person approves one draft and asks for it to be listed. The product goes up on
display ("ON") only when the person asked for that (the desk's "Show in the store right away" box);
otherwise it is hidden (SUSPENSION) until someone switches it on in the store centre.

Steps for one draft:
  1. Search the store for our seller code, so a product an earlier attempt created is recorded, not doubled.
  2. Read the item again on Domeggook: it must still be on sale, allow open-market resale and allow
     its images to be reused.
  3. Copy the store-wide settings (delivery, return address, A/S) from an existing product of ours.
  4. Upload the main image and the detail images to Naver's image server.
  5. POST /v2/products, with the approved introduction above the supplier's pictures.

Command line:
    python listing.py 3 --categories                 # suggest Naver categories for draft 3
    python listing.py 3 --dry-run                    # build the request and print it; nothing is sent
    python listing.py 3 --category 50006668          # register it hidden
    python listing.py 3 --category 50006668 --show   # register it on display
"""
import argparse
import html
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

import db
import drafts
import sourcing
from naver import api

HERE = Path(__file__).parent
EXPORTS = HERE / "exports"
CATEGORY_CACHE = EXPORTS / "naver_categories.json"
ORIGIN_CACHE = EXPORTS / "naver_origins.json"
CACHE_DAYS = 7
PAUSE = 0.6               # Naver allows 2 calls a second
STOCK = 99                # we resell from the supplier, so stock is a ceiling, not a count
MAX_DETAIL_IMAGES = 20    # detail images copied to Naver's server; the rest keep their original address
NOT_STATED = "상품상세참조"


class ListingError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status   # Naver's HTTP status when Naver answered with an error


class ListingUncertain(ListingError):
    """The product request went out but no clear answer came back: Naver may have created the product."""


def naver(method, path, **kwargs):
    """Call Naver, wait out the rate limit, and turn an error answer into a readable ListingError."""
    r = api(method, path, **kwargs)
    time.sleep(PAUSE)
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            raise ListingError(f"Naver answered {r.status_code}: {r.text[:300]}", r.status_code)
        problems = [f"{p.get('name', '')}: {p.get('message', '')}".strip(": ") for p in body.get("invalidInputs") or []]
        message = body.get("message") or body.get("code") or r.text[:300]
        raise ListingError(f"Naver answered {r.status_code}: {message}" + (" | " + "; ".join(problems) if problems else ""),
                           r.status_code)
    return r.json() if r.content else {}


def seller_code(item_id):
    """Our code on every listed product: it lets the desk find a product an earlier attempt created."""
    return f"G3-{item_id}"


def find_listed(item_id):
    """The store's product carrying our seller code for this item, as {'origin', 'channel', 'display'}, or None."""
    code = seller_code(item_id)
    answer = naver("POST", "/v1/products/search",
                   json={"searchKeywordType": "SELLER_CODE", "sellerManagementCode": code, "page": 1, "size": 10})
    for product in answer.get("contents") or []:
        for channel in product.get("channelProducts") or []:
            if channel.get("sellerManagementCode") == code and channel.get("statusType") != "DELETE":
                return {"origin": product.get("originProductNo"), "channel": channel.get("channelProductNo"),
                        "display": channel.get("channelProductDisplayStatusType")}
    return None


def cached(path, fetch):
    """Read a JSON list from exports/, refreshing it from Naver when it is older than CACHE_DAYS."""
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_DAYS * 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    EXPORTS.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def categories():
    return cached(CATEGORY_CACHE, lambda: naver("GET", "/v1/categories"))


def origins():
    return cached(ORIGIN_CACHE, lambda: naver("GET", "/v1/product-origin-areas")["originAreaCodeNames"])


# ---------------------------------------------------------------- supplier item
def dome_item(item_id):
    """The Domeggook item, reduced to what a listing needs."""
    data = sourcing.call({"ver": "4.6", "mode": "getItemView", "no": item_id})

    def dig(path, default=None):
        node = data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    parents = dig("category.parents.elem") or []
    if isinstance(parents, dict):
        parents = [parents]
    path = [p.get("name", "") for p in parents if isinstance(p, dict)] + [(dig("category.current") or {}).get("name", "")]
    return {
        "no": str(item_id),
        "on_sale": dig("basis.status") == "판매중",
        "deny_market": sourcing.truthy(dig("etc.denyMarket")),
        "images_usable": sourcing.truthy(dig("desc.license.usable")),
        "image": dig("thumb.original") or dig("thumb.large") or "",
        "detail_html": dig("desc.contents.item") or "",
        "category_path": [p for p in path if p],
        "country": dig("detail.country") or "",
        "manufacturer": dig("detail.manufacturer") or "",
        "model": dig("detail.model") or "",
        "tax": dig("basis.tax") or "",
    }


def why_not(item):
    """Reasons the supplier does not allow this listing, or an empty list."""
    reasons = []
    if not item["on_sale"]:
        reasons.append("the item is no longer on sale on Domeggook")
    if item["deny_market"]:
        reasons.append("the supplier does not allow resale on open markets")
    if not item["images_usable"]:
        reasons.append("the supplier does not allow its images to be reused")
    if not item["image"]:
        reasons.append("the item has no main image")
    return reasons


# ---------------------------------------------------------------- category and origin
def words(text):
    return {w for w in re.split(r"[\s>/,()·\-]+", text) if len(w) >= 2}


def suggest_categories(title, category_path, limit=6):
    """Naver leaf categories ranked by how well they match the supplier's category and our title.

    Domeggook's top level ("취미/도서") is its own grouping and misleads, so it is skipped; deeper
    supplier levels count for more. A person still chooses from the list."""
    squash = lambda s: s.replace(" ", "")
    levels = [words(squash(p)) for p in category_path[1:]][::-1]          # deepest first
    weights = [4, 3, 2, 1]
    supplier_leaf = squash(category_path[-1]) if category_path else ""
    squashed_title = squash(title)
    ranked = []
    for c in categories():
        if not c.get("last"):
            continue
        whole = squash(c["wholeCategoryName"])
        leaf = whole.split(">")[-1]
        naver_words = words(whole)
        score = sum(w * len(level & naver_words) for w, level in zip(weights, levels))
        if supplier_leaf and leaf == supplier_leaf:
            score += 6
        score += 2 * sum(1 for w in words(leaf) if w in squashed_title)
        if len(leaf) >= 2 and leaf in squashed_title:   # the title names this exact category, e.g. 넥워머
            score += 7
        if score > 0:
            ranked.append((score, -whole.count(">"), c["wholeCategoryName"], c["id"]))
    ranked.sort(reverse=True)
    return [{"id": cid, "name": name} for _, _, name, cid in ranked[:limit]]


def origin_info(country, importer):
    """Map Domeggook's "수입산_아시아_중국" to Naver's code for "수입산:아시아>중국"."""
    name = country.replace("_", ":", 1).replace("_", ">") if country else ""
    codes = {o["name"]: o["code"] for o in origins()}
    if name in codes:
        info = {"originAreaCode": codes[name], "content": name.split(":")[-1].replace(">", " "), "plural": False}
        if codes[name].startswith("02"):
            info["importer"] = importer or NOT_STATED
        return info
    return {"originAreaCode": "03", "content": "상세설명에 표시", "plural": False}   # "shown in the description"


# ---------------------------------------------------------------- images
def fetch_image(url):
    r = requests.get(url, timeout=30, headers={"Referer": "https://domeggook.com/", "User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    kind = sniff(r.content)   # Domeggook's CDN labels images application/octet-stream, so look at the bytes
    if not kind:
        raise ListingError(f"{url} is not a JPEG, PNG or GIF image")
    return r.content, kind


def sniff(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def upload_images(urls):
    """Copy images to Naver's image server. Returns {original url: Naver url} for those that worked."""
    done = {}
    for start in range(0, len(urls), 10):   # at most 10 per call
        batch, files = urls[start:start + 10], []
        for i, url in enumerate(batch):
            try:
                content, kind = fetch_image(url)
            except (requests.RequestException, ListingError):
                continue
            ext = {"image/png": "png", "image/gif": "gif"}.get(kind, "jpg")
            files.append((url, ("imageFiles", (f"img{start + i}.{ext}", content, kind))))
        if not files:
            continue
        body = naver("POST", "/v1/product-images/upload", files=[f for _, f in files])
        for (url, _), image in zip(files, body.get("images") or []):
            if image.get("url"):
                done[url] = image["url"]
    return done


IMG_SRC = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*["'])([^"']+)(["'])""", re.I)


def detail_images(supplier_html):
    seen = []
    for m in IMG_SRC.finditer(supplier_html):
        url = m.group(2).strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http") and url not in seen:
            seen.append(url)
    return seen


def intro_html(title, description):
    """The approved introduction as a short block above the supplier's pictures ('' when there is none)."""
    lines = [line.strip() for line in (description or "").splitlines() if line.strip()]
    if not lines:
        return ""
    items = "".join(f'<li style="margin:4px 0">{html.escape(line)}</li>' for line in lines)
    return ('<div style="max-width:860px;margin:0 auto 32px;padding:24px 28px;text-align:left;font-size:16px;'
            'line-height:1.7;border:1px solid #e5e7eb;border-radius:12px">'
            f'<h3 style="margin:0 0 12px;font-size:20px">{html.escape(title)}</h3>'
            f'<ul style="margin:0;padding-left:20px">{items}</ul></div>')


def detail_content(supplier_html, hosted, intro=""):
    """Our introduction, then the supplier's description with its images pointed at Naver's copies."""
    def swap(m):
        url = m.group(2).strip()
        url = "https:" + url if url.startswith("//") else url
        return m.group(1) + hosted.get(url, url) + m.group(3)
    body = re.sub(r"<script\b.*?</script>", "", supplier_html, flags=re.I | re.S)
    return intro + '<div style="text-align:center">' + IMG_SRC.sub(swap, body) + "</div>"


# ---------------------------------------------------------------- store settings and the request
def template_product():
    """Our own product whose delivery, return address and A/S settings every new listing copies.

    LISTING_TEMPLATE_PRODUCT in .env names it. Otherwise: among products on sale, the A/S phone number
    most of them use, and the most recently changed product with that number."""
    number = os.getenv("LISTING_TEMPLATE_PRODUCT")
    if number:
        return naver("GET", f"/v2/products/origin-products/{number}")["originProduct"]
    found = naver("POST", "/v1/products/search", json={"page": 1, "size": 50})
    on_sale = []
    for c in found.get("contents") or []:
        channel = (c.get("channelProducts") or [{}])[0]
        if channel.get("statusType") == "SALE":
            on_sale.append((channel.get("modifiedDate", ""), c["originProductNo"]))
    if not on_sale:
        raise ListingError("No product is on sale to copy delivery and return settings from. "
                           "Set LISTING_TEMPLATE_PRODUCT in backend/.env.")
    products = []
    for modified, no in sorted(on_sale, reverse=True)[:6]:
        product = naver("GET", f"/v2/products/origin-products/{no}")["originProduct"]
        phone = (product.get("detailAttribute", {}).get("afterServiceInfo") or {}).get("afterServiceTelephoneNumber")
        products.append((modified, phone, product))
    phones = [phone for _, phone, _ in products if phone]
    common = max(set(phones), key=phones.count) if phones else None
    return next(product for _, phone, product in products if phone == common or common is None)


def store_settings(template):
    delivery = json.loads(json.dumps(template["deliveryInfo"]))
    delivery["deliveryAttributeType"] = "NORMAL"   # the supplier ships it, so no same-day promise
    delivery.pop("todayStockQuantity", None)
    detail = template.get("detailAttribute", {})
    service = dict(detail.get("afterServiceInfo") or {})
    service["afterServiceGuideContent"] = "문의는 네이버 톡톡으로 남겨 주시면 빠르게 답변드리겠습니다."
    return {"deliveryInfo": delivery, "afterServiceInfo": service,
            "purchaseReviewInfo": detail.get("purchaseReviewInfo") or {"purchaseReviewExposure": True}}


def allowed_tags(tags):
    """Drop tags Naver will not accept (category names, brands and the like)."""
    tags = [t.strip() for t in tags if t and t.strip()][:10]
    if not tags:
        return []
    answer = naver("GET", "/v2/tags/restricted-tags", params={"tags": ",".join(tags)})
    blocked = {row["tag"] for row in answer if row.get("restricted")} if isinstance(answer, list) else set()
    return [t for t in tags if t.replace(" ", "") not in blocked and t not in blocked]


def build_request(d, item, category_id, settings, image_url, detail_html, tags, display="ON"):
    phone = settings["afterServiceInfo"].get("afterServiceTelephoneNumber") or NOT_STATED
    landed = d["cost"] + d["shipping"]
    tags = [{"text": t} for t in tags]
    origin = {
        "originProduct": {
            "statusType": "SALE",
            "saleType": "NEW",
            "leafCategoryId": str(category_id),
            "name": d["title"],
            "detailContent": detail_html,
            "images": {"representativeImage": {"url": image_url}},
            "salePrice": int(d["price"]),
            "stockQuantity": STOCK,
            "deliveryInfo": settings["deliveryInfo"],
            "detailAttribute": {
                "naverShoppingSearchInfo": {"manufacturerName": item["manufacturer"] or NOT_STATED, "catalogMatchingYn": False,
                                            **({"modelName": item["model"]} if item["model"] else {})},
                "afterServiceInfo": settings["afterServiceInfo"],
                "originAreaInfo": origin_info(item["country"], NOT_STATED),   # the importer is not named by the supplier
                "sellerCodeInfo": {
                    "sellerManagementCode": seller_code(d["item_id"]),
                    "sellerCustomCode1": f"{'도매매' if d['supplier'] == 'domeme' else '도매꾹'}-{item['no']}",
                    "sellerCustomCode2": f"원가{landed}",   # read back by store.py for the margin watch
                },
                "purchaseReviewInfo": settings["purchaseReviewInfo"],
                "taxType": "DUTYFREE" if "면세" in item["tax"] else "TAX",
                "minorPurchasable": True,
                "itselfProductionProductYn": False,
                "productInfoProvidedNotice": {
                    "productInfoProvidedNoticeType": "ETC",
                    "etc": {
                        "returnCostReason": NOT_STATED, "noRefundReason": NOT_STATED,
                        "qualityAssuranceStandard": NOT_STATED, "compensationProcedure": NOT_STATED,
                        "troubleShootingContents": NOT_STATED,
                        "itemName": d["title"][:50], "modelName": item["model"] or NOT_STATED,
                        "manufacturer": item["manufacturer"] or NOT_STATED,
                        "customerServicePhoneNumber": phone,
                    },
                },
                "seoInfo": {"sellerTags": tags} if tags else {},
            },
        },
        "smartstoreChannelProduct": {
            "naverShoppingRegistration": True,
            "channelProductDisplayStatusType": display,   # ON: visible at once; SUSPENSION: hidden until switched on
            "storeKeepExclusiveProduct": False,
        },
    }
    return origin


# ---------------------------------------------------------------- entry points
def approved_draft(con, draft_id):
    d = db.get_draft(con, draft_id)
    if d is None:
        raise ListingError(f"Draft {draft_id} does not exist.")
    if d["status"] != "approved":
        raise ListingError(f"Draft {draft_id} is {d['status']}; only approved drafts can be listed.")
    if d["supplier"] not in drafts.LISTABLE:
        raise ListingError(f"Draft {draft_id} comes from '{d['supplier']}'; only Domeggook and Domeme items can be listed.")
    return d


_supplier_paths = {}   # item id -> (time read, supplier category path); the Approvals page asks on every load


def supplier_path(item_id):
    hit = _supplier_paths.get(item_id)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    path = dome_item(item_id)["category_path"]
    _supplier_paths[item_id] = (time.time(), path)
    return path


def category_options(con, draft_id):
    d = db.get_draft(con, draft_id)
    if d is None:
        raise ListingError(f"Draft {draft_id} does not exist.")
    path = supplier_path(d["item_id"]) if d["supplier"] in drafts.LISTABLE else []
    return {"supplier_category": " > ".join(path), "options": suggest_categories(d["title"], path)}


def check_category(category_id):
    """Raise ListingError unless category_id is a Naver leaf category."""
    if not re.fullmatch(r"\d{5,}", str(category_id or "")):
        raise ListingError("Choose a Naver category first.")
    if str(category_id) not in {c["id"] for c in categories() if c.get("last")}:
        raise ListingError(f"{category_id} is not a Naver leaf category.")


def save_listed(con, draft_id, listing, by, how):
    """Record the store product on the draft. A locked database is retried, because the product exists either way."""
    for attempt in range(4):
        try:
            db.set_listed(con, draft_id, listing)
            break
        except sqlite3.OperationalError:
            if attempt == 3:
                raise ListingUncertain(f"Naver has product {listing['origin_product_no']}, but the desk could not save it. "
                                       "Press Register again to record it.")
            time.sleep(2)
    try:   # the product is recorded; a missing log line must not turn that into an error
        db.log_action(con, "draft_listed", f"draft_id={draft_id} product={listing['origin_product_no']} {how}"
                                           + (f" by={by}" if by else ""))
    except sqlite3.Error:
        pass
    return listing


def register(con, draft_id, category_id, by=None, dry_run=False, display="SUSPENSION"):
    """List one approved draft, on display ("ON") or hidden ("SUSPENSION"). Returns a dict describing what happened.

    Safe to repeat: the store is searched for our seller code first, so a product that an earlier attempt
    created (its answer lost to a timeout) is recorded instead of being created a second time."""
    if display not in ("ON", "SUSPENSION"):
        raise ListingError(f"Unknown display setting '{display}'.")
    d = approved_draft(con, draft_id)
    check_category(category_id)
    existing = find_listed(d["item_id"])
    if existing:
        listing = {"origin_product_no": existing["origin"], "channel_product_no": existing["channel"],
                   "category_id": str(category_id), "display": existing["display"], "listed_at": db.now(),
                   "listed_by": by, "found_in_store": True}
        if dry_run:
            return {"dry_run": True, "already_listed": listing}
        return save_listed(con, draft_id, listing, by, "found in the store from an earlier attempt")
    if d["listing"]:   # the store has no product with our code, so an earlier "sending"/"unknown" marker is void
        db.set_listing_state(con, draft_id, None)
    item = dome_item(d["item_id"])
    reasons = why_not(item)
    if reasons:
        raise ListingError("Cannot list this item: " + "; ".join(reasons) + ".")

    settings = store_settings(template_product())
    pictures = detail_images(item["detail_html"])[:MAX_DETAIL_IMAGES]
    if dry_run:
        hosted = {}
        main = "<uploaded at listing time>"
    else:
        hosted = upload_images([item["image"]] + pictures)
        main = hosted.get(item["image"])
        if not main:
            raise ListingError("Naver did not accept the main image.")
    tags = allowed_tags(json.loads(d["tags"] or "[]"))
    detail = detail_content(item["detail_html"], hosted, intro_html(d["title"], d["description"]))
    request = build_request(d, item, category_id, settings, main, detail, tags, display)
    if dry_run:
        return {"dry_run": True, "request": request, "detail_images": len(pictures)}

    # From here on a failure may leave a product in the store, so the draft carries a marker until the answer is in.
    db.set_listing_state(con, draft_id, {"state": "sending", "since": db.now(), "by": by})

    def unclear(why):
        try:   # if even this write fails, the "sending" marker stays and means the same thing
            db.set_listing_state(con, draft_id, {"state": "unknown", "since": db.now(), "by": by})
        except sqlite3.Error:
            pass
        return ListingUncertain("Naver gave no clear answer, so the product may be in the store already. Check the store "
                                "centre; Register searches the store first, so pressing it again will not create a "
                                f"second copy ({why})")

    try:
        answer = naver("POST", "/v2/products", json=request)
    except ListingError as e:
        if (e.status or 0) >= 500:   # a gateway error can hide a product that was created
            raise unclear(e)
        db.set_listing_state(con, draft_id, None)   # Naver refused the request: nothing was created
        raise
    except Exception as e:   # timeout, dropped connection, or an answer that is not JSON
        raise unclear(type(e).__name__)
    if not answer.get("originProductNo"):
        raise unclear("no product number in the answer")
    listing = {
        "origin_product_no": answer.get("originProductNo"),
        "channel_product_no": answer.get("smartstoreChannelProductNo"),
        "category_id": str(category_id), "display": display,
        "listed_at": db.now(), "listed_by": by,
        "detail_images_hosted": sum(1 for p in pictures if p in hosted), "detail_images": len(pictures),
    }
    return save_listed(con, draft_id, listing, by, "on display" if display == "ON" else "hidden")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List an approved draft in the smart store, hidden until switched on")
    parser.add_argument("draft_id", type=int)
    parser.add_argument("--category", help="Naver leaf category id")
    parser.add_argument("--categories", action="store_true", help="only suggest categories")
    parser.add_argument("--dry-run", action="store_true", help="build the request and print it; send nothing")
    parser.add_argument("--show", action="store_true", help="put it on display at once (default: hidden)")
    args = parser.parse_args()
    con = db.connect()
    try:
        if args.categories:
            print(json.dumps(category_options(con, args.draft_id), ensure_ascii=False, indent=2))
        else:
            result = register(con, args.draft_id, args.category, by="cli", dry_run=args.dry_run,
                              display="ON" if args.show else "SUSPENSION")
            if args.dry_run:
                req = json.loads(json.dumps(result["request"]))
                req["originProduct"]["detailContent"] = f"<{len(req['originProduct']['detailContent'])} chars>"
                print(json.dumps(req, ensure_ascii=False, indent=2))
                print(f"detail images found: {result['detail_images']}")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ListingError, sourcing.SourcingError) as e:
        sys.exit(f"Not listed: {e}")
