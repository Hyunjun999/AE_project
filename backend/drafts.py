"""Draft logic shared by the command-line scripts and the web API.

generate(): candidates.csv -> Gate 0 (price floor) -> titles and tags -> drafts saved as 'pending'.
decide():   a person approves, edits or rejects one pending draft.
Nothing in this module publishes to the store.
"""
import csv
import json
from pathlib import Path

import db
import llm
from price import contribution, price_floor

CANDIDATES_CSV = Path(__file__).parent / "candidates.csv"
BATCH = 5            # products per LLM call (instructions are shared across the batch)
MAX_TITLE = 50       # characters
BANNED = ["최고", "1위", "최저가", "특가", "정품", "100%"]                       # claims we never make
REVIEW_WORDS = ["유아", "아동", "어린이", "식품", "건강", "의료", "화장품",       # regulated or risky areas:
                "충전", "배터리", "전기", "USB", "LED"]                          # a person must check these


def load_candidates(path=CANDIDATES_CSV):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["cost"] = int(row["cost"])
        row["shipping"] = int(row.get("shipping") or 0)
        row["target_price"] = int(row["target_price"]) if row.get("target_price") else None
    return rows


def formula_title(row):
    parts = [row.get("keyword1"), row.get("keyword2"), row.get("keyword3"), row.get("size")]
    return " ".join(p.strip() for p in parts if p and p.strip())[:MAX_TITLE]


def formula_tags(row):
    return [k.strip() for k in (row.get("keyword1"), row.get("keyword2"), row.get("keyword3")) if k and k.strip()]


def check_title(title, row):
    """Return a list of problems a person should look at."""
    flags = []
    if len(title) > MAX_TITLE:
        flags.append(f"title longer than {MAX_TITLE} chars")
    if row.get("keyword1") and row["keyword1"].replace(" ", "") not in title.replace(" ", ""):
        flags.append("main keyword missing from title")
    flags += [f"banned word '{w}'" for w in BANNED if w in title]
    hits = [w for w in REVIEW_WORDS if w in title or w in row["name"]]
    if hits:
        flags.append("check category rules: " + ", ".join(hits))
    return flags


def is_hard(flag):
    """Hard problems block approval; the others only ask for a closer look."""
    return flag.startswith("banned word") or flag.startswith("title longer")


def split_flags(text):
    return [f for f in (text or "").split("; ") if f]


def llm_titles(con, rows):
    """Ask the LLM for titles and tags for one batch. Returns {candidate id: {'title':..., 'tags': [...]}}."""
    items = [{"id": r["_id"], "name": r["name"], "material": r.get("material"), "size": r.get("size"),
              "options": r.get("options"),
              "keywords": [r.get("keyword1"), r.get("keyword2"), r.get("keyword3")]} for r in rows]
    system = ("You write Korean product titles for Naver SmartStore. Use only the facts given. "
              "No brand or character names, no superlatives. Output JSON only.")
    user = ("For each product return {\"id\": id, \"title\": title of at most 50 characters built as "
            "[main keyword] [2-3 long-tail keywords] [attribute or size], \"tags\": up to 8 Korean tags}. "
            "Return one JSON array.\n" + json.dumps(items, ensure_ascii=False))
    text = llm.chat(con, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    purpose="title_tags", max_tokens=150 * len(rows)).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return {item["id"]: item for item in json.loads(text)}


def generate(con, csv_path=CANDIDATES_CSV, use_llm=True):
    """Create drafts from the candidates file. Returns {'events': [{'kind', 'item_id', 'message'}], 'created': n}."""
    events, passed = [], []

    def event(kind, item_id, message):
        events.append({"kind": kind, "item_id": item_id, "message": message})

    if not Path(csv_path).exists():
        event("error", "", f"No candidates file at {csv_path}. Create it first.")
        return {"events": events, "created": 0}

    for row in load_candidates(csv_path):
        floor = price_floor(row["cost"] + row["shipping"])
        price = row["target_price"] or floor
        if price < floor:
            event("gate0_reject", row["item_id"], f"target {price} is below the floor {floor}")
            db.log_action(con, "gate0_reject", f"{row['supplier']}/{row['item_id']} target={price} floor={floor}")
            continue
        row["_id"] = db.upsert_candidate(con, row)
        if db.has_open_draft(con, row["_id"]):
            event("skip", row["item_id"], "a draft already exists")
            continue
        row["_floor"], row["_price"] = floor, price
        passed.append(row)

    created = 0
    for start in range(0, len(passed), BATCH):
        batch = passed[start:start + BATCH]
        answers = {}
        if use_llm:
            try:
                answers = llm_titles(con, batch)
            except llm.TokenCapReached as e:
                event("notice", "", f"Token cap reached, using formula titles: {e}")
            except Exception as e:  # network error, bad JSON, etc.: fall back, never stop the run
                event("notice", "", f"LLM call failed, using formula titles: {e!r}")
        for row in batch:
            answer = answers.get(row["_id"])
            if answer and answer.get("title"):
                title, tags, source = answer["title"].strip(), answer.get("tags") or [], "llm"
            else:
                title, tags, source = formula_title(row), formula_tags(row), "formula"
            flags = check_title(title, row)
            db.add_draft(con, row["_id"], title, tags, row["_price"], row["_floor"], source, "; ".join(flags))
            created += 1
            profit, margin = contribution(row["_price"], row["cost"] + row["shipping"])
            event("draft", row["item_id"],
                  f"({source}) {title} | price {row['_price']} floor {row['_floor']} "
                  f"profit {profit} margin {margin:.1%}" + (f" | FLAGS: {'; '.join(flags)}" if flags else ""))
    db.log_action(con, "drafts_generated", f"created={created} use_llm={use_llm}")
    return {"events": events, "created": created}


def decide(con, draft_id, action, reviewer, new_title=None):
    """Approve or reject one pending draft. Returns (ok, message)."""
    d = db.get_draft(con, draft_id)
    if d is None:
        return False, f"Draft {draft_id} does not exist."
    if d["status"] != "pending":
        return False, f"Draft {draft_id} is already {d['status']}."
    if action == "reject":
        db.set_decision(con, draft_id, "rejected", reviewer)
        return True, "Rejected"
    if action != "approve":
        return False, f"Unknown action '{action}'."

    new_title = (new_title or "").strip()
    if new_title and new_title != d["title"]:
        flags = check_title(new_title, {"keyword1": d["keyword1"], "name": d["name"]})
        hard = [f for f in flags if is_hard(f)]
        if hard:
            return False, "The title breaks a hard rule: " + "; ".join(hard)
        db.set_decision(con, draft_id, "approved", reviewer, title=new_title, flags="; ".join(flags))
        return True, "Approved with the edited title"

    hard = [f for f in split_flags(d["flags"]) if is_hard(f)]
    if hard:
        return False, "The title breaks a hard rule: " + "; ".join(hard) + ". Edit it or reject it."
    db.set_decision(con, draft_id, "approved", reviewer)
    return True, "Approved"


def to_view(d):
    """Turn a draft row into a plain dict for the page, the agent and the terminal."""
    landed = d["cost"] + d["shipping"]
    profit, margin = contribution(d["price"], landed)
    flags = split_flags(d["flags"])
    return {
        "id": d["id"], "status": d["status"], "source": d["source"],
        "supplier": d["supplier"], "item_id": d["item_id"], "name": d["name"],
        "title": d["title"], "tags": json.loads(d["tags"]),
        "cost": landed, "floor": d["floor"], "price": d["price"],
        "profit": profit, "margin_pct": round(margin * 100, 1),
        "flags": flags, "blocked": any(is_hard(f) for f in flags),
        "decided_by": d["decided_by"], "decided_at": d["decided_at"],
    }
