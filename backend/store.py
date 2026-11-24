"""Read-only view of the store. Cached for a minute so pages do not hammer the Commerce API."""
import time

PAGE_SIZE = 50
MAX_PAGES = 10          # up to 500 products
_cache = {"at": 0.0, "data": None}


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
                    "status": ch.get("statusType"),
                    "price": ch.get("salePrice"),
                    "stock": ch.get("stockQuantity"),
                })
        if len(contents) < PAGE_SIZE:
            break
        time.sleep(0.6)  # the API allows 2 requests per second
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