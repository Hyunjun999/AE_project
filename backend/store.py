"""Read-only view of the store. Cached for a minute so pages do not hammer the Commerce API."""
import re
import time

PAGE_SIZE = 50
MAX_PAGES = 10          # up to 500 products
COST_TTL = 600          # seconds a product's cost is remembered before it is read again
COST_PATTERN = re.compile(r"원가\s*([\d,]+)")   # the team writes e.g. "원가8600" in a seller code field
_cache = {"at": 0.0, "data": None}
_costs = {}             # product no -> (time read, cost or None)


def cost_from_codes(codes):
    """Find "원가<number>" in the seller code fields. Returns an int, or None when it is not written there."""
    for value in (codes or {}).values():
        match = COST_PATTERN.search(str(value))
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _cost(api, product_no):
    cached = _costs.get(product_no)
    if cached and time.time() - cached[0] < COST_TTL:
        return cached[1]
    time.sleep(0.6)  # the API allows 2 requests per second
    r = api("GET", f"/v2/products/origin-products/{product_no}")
    if r.status_code != 200:
        return cached[1] if cached else None
    codes = r.json().get("originProduct", {}).get("detailAttribute", {}).get("sellerCodeInfo", {})
    cost = cost_from_codes(codes)
    _costs[product_no] = (time.time(), cost)
    return cost


def _fetch():
    from naver import api  # imported here so the rest of the app works without Naver keys

    products = []
    for page in range(1, MAX_PAGES + 1):
        r = api("POST", "/v1/products/search", json={"page": page, "size": PAGE_SIZE})
        if r.status_code != 200:
            return {"error": f"Commerce API returned {r.status_code}"}
        contents = r.json().get("contents", [])
        for item in contents:
            for ch in item.get("channelProducts", []):
                products.append({
                    "no": item.get("originProductNo"),
                    "name": ch.get("name"),
                    # on sale but with its display off (e.g. just listed by the desk) counts as hidden, not on sale
                    "status": "HIDDEN" if ch.get("statusType") == "SALE" and ch.get("channelProductDisplayStatusType") == "SUSPENSION"
                              else ch.get("statusType"),
                    "price": ch.get("salePrice"),
                    "stock": ch.get("stockQuantity"),
                    "code": ch.get("sellerManagementCode") or "",   # G3-<Domeggook item> on products the desk listed
                })
        if len(contents) < PAGE_SIZE:
            break
        time.sleep(0.6)  # the API allows 2 requests per second
    for product in products:
        if product["status"] == "SALE":
            product["cost"] = _cost(api, product["no"])
    counts = {}
    for p in products:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return {"count_by_status": counts, "products": products, "fetched_at": time.strftime("%H:%M:%S")}


def snapshot(max_age=60):
    """Return {'count_by_status', 'products', 'fetched_at'} or {'error': ...}. Never raises."""
    if _cache["data"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    try:
        data = _fetch()
    except Exception as e:  # no network, missing keys, IP not allowed, ...
        return {"error": f"store data unavailable: {e!r}"}
    if "error" not in data:
        _cache.update(at=time.time(), data=data)
    return data
