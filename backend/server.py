"""Web app: JSON API under /api and the pages in ../frontend. Standard library only.

Local use:
    python server.py            # then open http://localhost:8000
Hosted use (Render and similar): set HOST=0.0.0.0 and APP_PASSWORD. The server refuses to
listen on a public address without a password.
"""
import base64
import getpass
import hmac
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import agent
import db
import drafts
import listing
import llm
import orders
import social
import sourcing
import store
from price import FEE_RATE, MIN_MARGIN, contribution, price_floor

HOST, PORT = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000"))
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
AUTO_PUBLISH_MINUTES = int(os.getenv("AUTO_PUBLISH_MINUTES", "0"))  # 0 = publish only by hand
MISSION = {"deadline": os.getenv("MISSION_DEADLINE", "2026-12-23"), "orders": int(os.getenv("MISSION_ORDERS", "100"))}
FRONTEND = (Path(__file__).parent.parent / "frontend").resolve()
REVIEWER = os.getenv("REVIEWER") or getpass.getuser()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- API functions
def with_margins(shop):
    """Add floor, profit and margin to every on-sale product whose cost is known. Returns a copy."""
    if "error" in shop:
        return shop
    products = []
    for p in shop["products"]:
        p = dict(p)
        if p.get("cost") and p.get("price"):
            profit, margin = contribution(p["price"], p["cost"])
            p.update(floor=price_floor(p["cost"]), profit=profit, margin_pct=round(margin * 100, 1))
        products.append(p)
    return {**shop, "products": products}


def overview(_query, body):
    con = db.connect()
    orders.sync(con)
    return {
        "store": with_margins(store.snapshot()),
        "orders": orders.tally(con),
        "mission": MISSION,
        "drafts": db.draft_counts(con),
        "tokens": {"used": llm.used_this_week(con), "cap": llm.WEEKLY_CAP, "week_start": llm.week_start(),
                   "model": llm.MODEL},
        "rules": {"fee_rate": FEE_RATE, "min_margin": MIN_MARGIN, "max_title": drafts.MAX_TITLE},
        "reviewer": body.get("_user") or REVIEWER,  # whoever signed in; REVIEWER on a local desk and in the snapshot
    }


def list_drafts(query, _body):
    status = query.get("status", ["pending"])[0]
    if status not in ("pending", "approved", "rejected", "published"):
        raise ApiError(400, f"Unknown status '{status}'.")
    con = db.connect()
    return [drafts.to_view(d) for d in db.drafts_by_status(con, status)]


def generate_drafts(_query, body):
    con = db.connect()
    return drafts.generate(con, use_llm=bool(body.get("use_llm")))


def decide(draft_id, action, body):
    con = db.connect()
    ok, message = drafts.decide(con, draft_id, action, body.get("_user") or REVIEWER, body.get("title"), body.get("price"))
    if not ok:
        raise ApiError(409, message)
    return {"message": message, "drafts": db.draft_counts(con)}


def list_in_store(draft_id, body):
    """Register one approved draft in the store with its display off."""
    try:
        result = listing.register(db.connect(), draft_id, body.get("category_id"), by=body.get("_user"))
    except (listing.ListingError, sourcing.SourcingError) as e:
        raise ApiError(409, str(e))
    return {"message": f"Registered as product {result['origin_product_no']}, hidden until you switch it on.",
            "listing": result}


def category_options(draft_id):
    try:
        return listing.category_options(db.connect(), draft_id)
    except (listing.ListingError, sourcing.SourcingError) as e:
        raise ApiError(409, str(e))


def social_status(draft_id, _body):
    return {"posts": social.posts_for(db.connect(), draft_id), "paused": social.is_paused(db.connect())}


def social_retry(draft_id, _body):
    """Post (or retry a failed post of) one registered draft, in the background."""
    con = db.connect()
    d = db.get_draft(con, draft_id)
    if d is None or d["status"] != "published":
        raise ApiError(409, "Only drafts registered in the store can be posted.")
    if social.is_paused(con):
        raise ApiError(409, "Social posting is paused.")
    social.start(draft_id, retry=True)
    return {"message": "Posting started. Results appear in the activity log within a minute or two."}


def source(_query, body):
    con = db.connect()
    return sourcing.run(con, str(body.get("keyword") or ""), str(body.get("market") or "dome"),
                        body.get("look") or 10, body.get("picks") or 3, use_llm=bool(body.get("use_llm")), by=body.get("_user"),
                        max_moq=body.get("max_moq"))


def sourcing_runs(_query, _body):
    return db.recent_runs(db.connect(), 5)


def price_check(query, _body):
    def number(name, default=0):
        raw = query.get(name, [""])[0].strip()
        if raw == "":
            return default
        try:
            value = int(float(raw))
        except ValueError:
            raise ApiError(400, f"'{name}' must be a number.")
        if value < 0:
            raise ApiError(400, f"'{name}' cannot be negative.")
        return value

    cost, shipping, ad = number("cost"), number("shipping"), number("ad")
    target = number("target", None)
    if cost <= 0:
        raise ApiError(400, "Enter the supplier cost.")
    landed = cost + shipping
    floor = price_floor(landed, ad)
    price = target if target else floor
    profit, margin = contribution(price, landed, ad)
    return {
        "landed_cost": landed, "ad": ad, "floor": floor, "price": price, "has_target": bool(target),
        "fee": round(price * FEE_RATE), "profit": profit, "margin_pct": round(margin * 100, 1),
        "clears": price >= floor, "gap": price - floor,
    }


def ask(_query, body):
    question = (body.get("question") or "").strip()
    if not question:
        raise ApiError(400, "Type a question first.")
    return agent.ask(question, by=body.get("_user"))


def activity(query, _body):
    con = db.connect()
    limit = min(int(query.get("limit", ["50"])[0]), 200)
    return [dict(r) for r in db.recent_actions(con, limit)]


def egress_ip(_query, _body):
    """The public IP this server calls out from: the address to register in the Naver Commerce API center."""
    import requests
    try:
        return {"ip": requests.get("https://api.ipify.org", timeout=10).text.strip()}
    except requests.RequestException as e:
        raise ApiError(502, f"Could not look up the outbound IP: {e}")


GET_ROUTES = {"/api/overview": overview, "/api/drafts": list_drafts, "/api/price": price_check,
              "/api/activity": activity, "/api/egress-ip": egress_ip, "/api/sourcing": sourcing_runs}
POST_ROUTES = {"/api/drafts/generate": generate_drafts, "/api/ask": ask, "/api/source": source}
DECISION = re.compile(r"^/api/drafts/(\d+)/(approve|reject)$")
LIST = re.compile(r"^/api/drafts/(\d+)/list$")
CATEGORIES = re.compile(r"^/api/drafts/(\d+)/categories$")
SOCIAL = re.compile(r"^/api/drafts/(\d+)/social$")


# ---------------------------------------------------------------- HTTP plumbing
class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _user(self):
        """Return the signed-in name, or None after sending a 401. With no APP_PASSWORD set, everyone is REVIEWER."""
        if not APP_PASSWORD:
            return REVIEWER
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                name, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                name, password = "", ""
            if hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
                return name.strip() or REVIEWER
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Agentic desk", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def _api(self, fn, *args):
        try:
            self._send(200, fn(*args))
        except ApiError as e:
            self._send(e.status, {"error": e.message})
        except Exception as e:  # keep the page alive and show what went wrong
            self._send(500, {"error": f"Server error: {e!r}"})

    def _static(self, path):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (FRONTEND / relative).resolve()
        if FRONTEND not in target.parents or not target.is_file():
            self._send(404, {"error": "Not found"})
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
            kind += "; charset=utf-8"
        self._send(200, target.read_bytes(), kind)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz":  # for the host's health check; reveals nothing
            self._send(200, {"ok": True})
            return
        user = self._user()
        if user is None:
            return
        if url.path in GET_ROUTES:
            self._api(GET_ROUTES[url.path], parse_qs(url.query), {"_user": user})
        elif CATEGORIES.match(url.path):
            self._api(category_options, int(CATEGORIES.match(url.path).group(1)))
        elif SOCIAL.match(url.path):
            self._api(social_status, int(SOCIAL.match(url.path).group(1)), {})
        elif url.path.startswith("/api/"):
            self._send(404, {"error": "Unknown API path"})
        else:
            self._static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        user = self._user()
        if user is None:
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "The request body must be a JSON object."})
            return
        body["_user"] = user  # set by the server, never trusted from the client
        match = DECISION.match(url.path)
        if match:
            self._api(decide, int(match.group(1)), match.group(2), body)
        elif LIST.match(url.path):
            self._api(list_in_store, int(LIST.match(url.path).group(1)), body)
        elif SOCIAL.match(url.path):
            self._api(social_retry, int(SOCIAL.match(url.path).group(1)), body)
        elif url.path in POST_ROUTES:
            self._api(POST_ROUTES[url.path], {}, body)
        else:
            self._send(404, {"error": "Unknown API path"})

    def log_message(self, fmt, *args):  # keep the terminal quiet; actions are in the activity log
        pass


def auto_publish():
    """Every few minutes, push the desk's data for the public page, but only when something changed."""
    import publish

    delay = 10
    while True:
        time.sleep(delay)
        delay = AUTO_PUBLISH_MINUTES * 60
        try:
            message = publish.publish_if_changed(lambda: overview({}, {}))
        except Exception as e:
            message = f"failed: {e!r}"
        if message:
            print(f"[publish {time.strftime('%Y-%m-%d %H:%M')}] {message}", flush=True)


if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost", "::1") and not APP_PASSWORD:
        raise SystemExit("Refusing to start: HOST is public but APP_PASSWORD is not set.")
    lock = "password on" if APP_PASSWORD else "no password (local only)"
    print(f"Listening on {HOST}:{PORT} | {lock} | reviewer: {REVIEWER}  (Ctrl+C to stop)", flush=True)
    if AUTO_PUBLISH_MINUTES > 0:
        print(f"Publishing to the public page every {AUTO_PUBLISH_MINUTES} minutes when the data changes.", flush=True)
        threading.Thread(target=auto_publish, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
