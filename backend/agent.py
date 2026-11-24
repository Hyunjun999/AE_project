"""Answer questions about the store. One LLM call per question; the code gathers the data first.

The agent only reads. It never changes the store: it suggests actions for a person to approve.
Usage:
    python agent.py "Which drafts have the lowest margin?"
"""
import json
import sys

import db
import drafts
import llm
import orders
import store
from price import FEE_RATE, MIN_MARGIN

SYSTEM = (
    "You are the marketing agent for a small Naver SmartStore run by two students. "
    "Answer in the language of the question, in under 150 words. "
    "Use only the DATA given. If the data does not contain the answer, say what is missing. Never invent numbers. "
    "You cannot change the store; when an action is needed, propose it for a person to approve. "
    f"Rules: minimum contribution margin {MIN_MARGIN:.0%}; fee rate {FEE_RATE:.2%}; "
    "price floor = (cost + ad cost per sale) / (1 - fee - margin)."
)
KEEP = ("id", "status", "title", "price", "floor", "cost", "profit", "margin_pct", "flags")


def build_context(con):
    snap = store.snapshot()
    if "error" in snap:
        shop = snap
    else:
        shop = {"count_by_status": snap["count_by_status"],
                "on_sale": [{k: p[k] for k in ("no", "name", "price", "stock", "cost") if p.get(k) is not None}
                            for p in snap["products"] if p["status"] == "SALE"]}
    rows = [drafts.to_view(d) for s in ("pending", "approved") for d in db.drafts_by_status(con, s)]
    return {
        "store": shop,
        "drafts": [{k: v[k] for k in KEEP} for v in rows],
        "orders_counted": orders.tally(con)["count"],
        "llm_tokens": {"used_this_week": llm.used_this_week(con), "weekly_cap": llm.WEEKLY_CAP},
    }


def ask(question):
    """Return {'answer': str, 'tokens_used': int, 'tokens_cap': int}. Never raises."""
    con = db.connect()
    context = json.dumps(build_context(con), ensure_ascii=False, separators=(",", ":"))
    try:
        answer = llm.chat(
            con,
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"DATA:\n{context}\n\nQUESTION: {question}"}],
            purpose="chat", max_tokens=350,
        )
        db.add_chat(con, question[:500], answer)
    except llm.TokenCapReached as e:
        answer = f"Weekly token cap reached, so no LLM call was made. ({e})"
    except Exception as e:
        answer = f"The LLM call failed: {e!r}"
    db.log_action(con, "chat", question[:200])
    return {"answer": answer, "tokens_used": llm.used_this_week(con), "tokens_cap": llm.WEEKLY_CAP}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python agent.py "your question"')
        raise SystemExit(1)
    result = ask(" ".join(sys.argv[1:]))
    print(result["answer"])
    print(f"\n[tokens this week: {result['tokens_used']} of {result['tokens_cap']}]")
