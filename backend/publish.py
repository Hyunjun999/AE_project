"""Publish the desk's current data so the public page on github.io can show it.

GitHub Pages serves the files in this repository as they are, but it cannot run Python,
and a visitor's browser cannot reach the desk machine. So the numbers the page needs are
saved to one file, frontend/js/snapshot.js, and pushed. No keys and no buyer data are written.

Usage:
    python publish.py              # save the data, commit that one file, push
    python publish.py --no-push    # save the data only
The server can also do this by itself: set AUTO_PUBLISH_MINUTES in backend/.env.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import db
import drafts

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "frontend" / "js" / "snapshot.js"
PREFIX = "window.SNAPSHOT = "
STATUSES = ("pending", "approved", "rejected", "published")


class StoreUnavailable(Exception):
    pass


def build_snapshot(overview_fn=None):
    if overview_fn is None:
        import server  # imported here so the server can import this module without a loop
        overview_fn = lambda: server.overview({}, {})
    con = db.connect()
    overview = overview_fn()
    if "error" in overview["store"]:
        raise StoreUnavailable(overview["store"]["error"])
    return {
        "exported_at": dt.datetime.now().isoformat(timespec="minutes"),
        "overview": overview,
        "drafts": {s: [drafts.to_view(d) for d in db.drafts_by_status(con, s)] for s in STATUSES},
        "activity": [dict(r) for r in db.recent_actions(con, 100)],
        "chats": [dict(r) for r in db.recent_chats(con, 20)],
    }


def fingerprint(snapshot):
    """The snapshot without its timestamps, so two reads of unchanged data compare equal."""
    clean = json.loads(json.dumps(snapshot))
    clean.pop("exported_at", None)
    clean.get("overview", {}).get("store", {}).pop("fetched_at", None)
    return json.dumps(clean, sort_keys=True, ensure_ascii=False)


def published_fingerprint():
    try:
        text = TARGET.read_text(encoding="utf-8")
        body = text[text.index(PREFIX) + len(PREFIX):].strip().rstrip(";")
        data = json.loads(body)
        return fingerprint(data) if data else None
    except (OSError, ValueError):
        return None


def save(snapshot):
    TARGET.write_text(PREFIX + json.dumps(snapshot, ensure_ascii=False) + ";\n", encoding="utf-8")


def push(stamp, ask_for_login=True):
    """Commit only the data file and push. Returns a one-line result."""
    env = dict(os.environ)
    if not ask_for_login:
        env["GIT_TERMINAL_PROMPT"] = "0"  # fail instead of waiting for a password nobody will type

    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True, env=env, timeout=120)

    relative = str(TARGET.relative_to(ROOT))
    git("add", relative)
    if git("diff", "--cached", "--quiet", "--", relative).returncode != 0:  # the data file has changes to commit
        commit = git("commit", "-m", f"Publish desk data ({stamp})", "--", relative)
        if commit.returncode != 0:
            return "Commit failed: " + (commit.stdout + commit.stderr).strip()
    result = git("push")
    if result.returncode != 0:
        return "Push failed: " + (result.stdout + result.stderr).strip()
    return "Pushed. The public page updates in about a minute."


def waiting_to_push():
    """True when a committed data file has not reached GitHub yet."""
    relative = str(TARGET.relative_to(ROOT))
    result = subprocess.run(["git", "-C", str(ROOT), "log", "--oneline", "@{u}..HEAD", "--", relative],
                            text=True, capture_output=True, timeout=60)
    return result.returncode == 0 and bool(result.stdout.strip())


def publish_if_changed(overview_fn=None):
    """Used by the server's timer. Returns a message, or None when nothing changed."""
    try:
        snapshot = build_snapshot(overview_fn)
    except StoreUnavailable as e:
        return f"Skipped: the store could not be read ({e})."
    if fingerprint(snapshot) == published_fingerprint():
        # nothing new; but an earlier push may have failed, so send it again if it is still waiting
        return push(snapshot["exported_at"], ask_for_login=False) if waiting_to_push() else None
    save(snapshot)
    return push(snapshot["exported_at"], ask_for_login=False)


def main():
    parser = argparse.ArgumentParser(description="Publish the desk's data for the public page")
    parser.add_argument("--no-push", action="store_true", help="save the data file but do not commit or push")
    args = parser.parse_args()

    try:
        snapshot = build_snapshot()
    except StoreUnavailable as e:
        raise SystemExit(f"Stopped: the store could not be read ({e}). Nothing was published.")
    save(snapshot)
    counts = snapshot["overview"]["drafts"]
    print(f"Saved {TARGET}")
    print(f"  products: {len(snapshot['overview']['store']['products'])} | drafts: {counts} | "
          f"activity rows: {len(snapshot['activity'])} | agent answers: {len(snapshot['chats'])}")
    if not args.no_push:
        print(push(snapshot["exported_at"]))


if __name__ == "__main__":
    main()
