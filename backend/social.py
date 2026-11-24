"""Post a newly registered product to Instagram and YouTube Shorts.

When listing.register() has put a draft in the Naver store, social.after_listing() is called. It
runs in the background, so a slow or failing post can never break the listing, and it does this:

  1. Write a caption (LLM with the weekly token cap, or a plain formula if the LLM is not available).
  2. Instagram: publish the product's main image as a feed post (Meta Graph API).
  3. YouTube: render a vertical 15-second video from the product images with ffmpeg, upload it as a Short.

Safety, in the same spirit as the rest of the desk:
  * Off unless SOCIAL_AUTOPOST=1 in backend/.env.
  * Kill switch:  python social.py --pause   (and --resume). While paused nothing is posted.
  * One post per draft per platform, ever (table social_posts, UNIQUE). A retry never posts twice.
  * A failure on one platform does not stop the other; it is saved and shown in the activity log.
  * Captions only use facts we already hold (title, tags, material, size, options) and never contain
    the words in drafts.BANNED. No buyer data is used.
  * YouTube videos are private unless YT_PRIVACY=public (Google also forces "private" for API projects
    that have not passed its audit).

Command line (run inside backend/):
    python social.py 3 --dry-run        # show the caption and render the video; post nothing
    python social.py 3                  # post draft 3 now (skips platforms already posted)
    python social.py 3 --retry          # try again where an earlier attempt failed
    python social.py --status           # what has been posted
    python social.py --pause / --resume
    python social.py --youtube-login    # one-time: get YT_REFRESH_TOKEN
See .env.social.example for every setting.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

import db
import drafts
import llm

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

OUT_DIR = HERE / "exports" / "social"
GRAPH = f"https://graph.facebook.com/{os.getenv('META_GRAPH_VERSION', 'v23.0')}"
PLATFORMS = ("instagram", "youtube")
MAX_CAPTION = 2200        # Instagram's limit
MAX_HASHTAGS = 8
IMAGES_IN_VIDEO = 5       # main image + up to 4 detail images
SECONDS_PER_IMAGE = 3     # 5 images -> 15 seconds
STALE_MINUTES = 15        # a post stuck in "posting" for this long is treated as failed


class SocialError(Exception):
    pass


# ---------------------------------------------------------------- settings
def enabled_platforms():
    names = [p.strip().lower() for p in os.getenv("SOCIAL_PLATFORMS", ",".join(PLATFORMS)).split(",")]
    return [p for p in PLATFORMS if p in names]


def configured(platform):
    """Platforms whose keys are in .env. Others are skipped quietly."""
    if platform == "instagram":
        return bool(os.getenv("IG_USER_ID") and os.getenv("IG_ACCESS_TOKEN"))
    if platform == "youtube":
        return bool(os.getenv("YT_CLIENT_ID") and os.getenv("YT_CLIENT_SECRET") and os.getenv("YT_REFRESH_TOKEN"))
    return False


def is_paused(con):
    return db.get_setting(con, "social_paused", "0") == "1"


# ---------------------------------------------------------------- state (one post per draft and platform)
def posts_for(con, draft_id):
    rows = con.execute("SELECT platform, status, url, external_id, error, updated_at FROM social_posts "
                       "WHERE draft_id = ? ORDER BY id", (draft_id,)).fetchall()
    return [dict(r) for r in rows]


def state_of(con, draft_id, platform):
    row = con.execute("SELECT status FROM social_posts WHERE draft_id = ? AND platform = ?",
                      (draft_id, platform)).fetchone()
    return row["status"] if row else None


def claim(con, draft_id, platform, retry):
    """Reserve the right to post. True for exactly one caller; this is what stops double posts."""
    ts = db.now()
    cur = con.execute("INSERT OR IGNORE INTO social_posts (draft_id, platform, status, created_at, updated_at) "
                      "VALUES (?, ?, 'posting', ?, ?)", (draft_id, platform, ts, ts))
    if cur.rowcount == 1:
        con.commit()
        return True
    stale = (time.time() - STALE_MINUTES * 60)
    stale_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stale))
    reusable = "status = 'failed' OR (status = 'posting' AND updated_at < ?)" if retry else \
               "(status = 'posting' AND updated_at < ?)"
    cur = con.execute(f"UPDATE social_posts SET status = 'posting', error = NULL, updated_at = ? "
                      f"WHERE draft_id = ? AND platform = ? AND ({reusable})", (ts, draft_id, platform, stale_iso))
    con.commit()
    return cur.rowcount == 1


def finish(con, draft_id, platform, status, url=None, external_id=None, error=None, caption=None):
    con.execute("UPDATE social_posts SET status = ?, url = ?, external_id = ?, error = ?, caption = ?, updated_at = ? "
                "WHERE draft_id = ? AND platform = ?",
                (status, url, external_id, error, caption, db.now(), draft_id, platform))
    con.commit()
    db.log_action(con, f"social_{status}", f"draft_id={draft_id} platform={platform}"
                       + (f" url={url}" if url else "") + (f" error={error}" if error else ""))


# ---------------------------------------------------------------- the words
def facts(con, d):
    row = con.execute("SELECT material, size, options FROM candidates WHERE id = ?", (d["candidate_id"],)).fetchone()
    return {k: row[k] for k in ("material", "size", "options") if row and row[k]}


def product_link(listing):
    store = os.getenv("STORE_URL", "").rstrip("/")     # e.g. https://smartstore.naver.com/yourstore
    no = (listing or {}).get("channel_product_no")
    return f"{store}/products/{no}" if store and no else ""


def hashtags(tags):
    seen = []
    for t in tags:
        tag = "#" + "".join(str(t).split()).lstrip("#")
        if len(tag) > 1 and tag not in seen:
            seen.append(tag)
    return seen[:MAX_HASHTAGS]


def formula_content(d, tags, link):
    lines = [d["title"]]
    if link:
        lines.append(f"스토어에서 확인하세요 👉 {link}")
    caption = "\n\n".join(lines + [" ".join(hashtags(tags))]).strip()
    return {"caption": caption[:MAX_CAPTION], "yt_title": d["title"][:90], "source": "formula"}


def llm_content(con, d, tags, known, link):
    system = ("You write short Korean social media captions for a Naver SmartStore product. Use only the facts given. "
              "No superlatives or claims such as 최고, 1위, 최저가, 특가, 정품, 100%. No price. No brand or character "
              "names. Output JSON only.")
    user = ("Return {\"caption\": 2-3 short friendly lines for an Instagram post, \"hashtags\": up to 8 Korean hashtags "
            "without the # sign, \"yt_title\": a YouTube Shorts title of at most 60 characters}.\n"
            + json.dumps({"title": d["title"], "tags": tags, "facts": known}, ensure_ascii=False))
    text = llm.chat(con, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    purpose="social_caption", max_tokens=350).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text)
    body = str(data["caption"]).strip()
    parts = [body] + ([f"스토어에서 확인하세요 👉 {link}"] if link else []) + [" ".join(hashtags(data.get("hashtags") or tags))]
    return {"caption": "\n\n".join(parts)[:MAX_CAPTION], "yt_title": str(data.get("yt_title") or d["title"])[:90],
            "source": "llm"}


def make_content(con, d, listing, use_llm):
    tags = json.loads(d["tags"] or "[]")
    link = product_link(listing)
    content = None
    if use_llm:
        try:
            content = llm_content(con, d, tags, facts(con, d), link)
        except Exception as e:      # token cap, network, bad JSON: fall back, never stop the post
            db.log_action(con, "social_caption_fallback", f"draft_id={d['id']} {e!r}")
    if content is not None:
        banned = [w for w in drafts.BANNED if w in content["caption"] or w in content["yt_title"]]
        if banned:
            db.log_action(con, "social_caption_fallback", f"draft_id={d['id']} banned words {banned}")
            content = None
    return content or formula_content(d, tags, link)


# ---------------------------------------------------------------- Instagram
def graph_json(r):
    try:
        body = r.json()
    except ValueError:
        raise SocialError(f"Meta answered {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or "error" in body:
        err = body.get("error", {})
        raise SocialError(f"Meta answered {r.status_code}: {err.get('message', r.text[:200])}")
    return body


def post_instagram(caption, image_url):
    """Publish one image. Instagram fetches the image itself, so the URL must be public and a JPEG."""
    uid, token = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    created = graph_json(requests.post(f"{GRAPH}/{uid}/media", timeout=60,
                                       data={"image_url": image_url, "caption": caption, "access_token": token}))
    container = created["id"]
    for _ in range(20):
        status = graph_json(requests.get(f"{GRAPH}/{container}", timeout=30,
                                         params={"fields": "status_code", "access_token": token}))
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise SocialError(f"Instagram could not process the image ({code}). It must be a public JPEG.")
        time.sleep(3)
    else:
        raise SocialError("Instagram took too long to process the image.")
    media_id = graph_json(requests.post(f"{GRAPH}/{uid}/media_publish", timeout=60,
                                        data={"creation_id": container, "access_token": token}))["id"]
    try:
        url = graph_json(requests.get(f"{GRAPH}/{media_id}", timeout=30,
                                      params={"fields": "permalink", "access_token": token})).get("permalink")
    except SocialError:
        url = None      # it is published; only the link is missing
    return media_id, url


# ---------------------------------------------------------------- YouTube
def make_video(urls, out_path):
    """A vertical 1080x1920 slideshow of the product images (blurred fill behind each image)."""
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, url in enumerate(urls[:IMAGES_IN_VIDEO]):
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
            except requests.RequestException:
                continue
            path = Path(tmp) / f"img{i}"
            path.write_bytes(r.content)
            files.append(path)
        if not files:
            raise SocialError("No product image could be downloaded for the video.")
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for path in files:
            cmd += ["-loop", "1", "-t", str(SECONDS_PER_IMAGE), "-i", str(path)]
        chains = [
            f"[{i}:v]split[a{i}][b{i}];"
            f"[a{i}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg{i}];"
            f"[b{i}]scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v{i}]"
            for i in range(len(files))
        ]
        chains.append("".join(f"[v{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=1:a=0[out]")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-filter_complex", ";".join(chains), "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-movflags", "+faststart", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            raise SocialError("ffmpeg is not installed on this machine (needed to make the YouTube video).")
        except subprocess.CalledProcessError as e:
            raise SocialError(f"ffmpeg failed: {e.stderr.strip()[-300:]}")
        except subprocess.TimeoutExpired:
            raise SocialError("ffmpeg took too long.")
    return out_path


def youtube_token():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "access_token" not in body:
        raise SocialError(f"Google refused the YouTube login: {body.get('error_description') or body.get('error') or r.status_code}. "
                          "Run python social.py --youtube-login again.")
    return body["access_token"]


def post_youtube(title, description, video_path, tags):
    token = youtube_token()
    size = video_path.stat().st_size
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                        "categoryId": os.getenv("YT_CATEGORY_ID", "22")},
            "status": {"privacyStatus": os.getenv("YT_PRIVACY", "private"), "selfDeclaredMadeForKids": False}}
    start = requests.post("https://www.googleapis.com/upload/youtube/v3/videos"
                          "?uploadType=resumable&part=snippet,status", timeout=60, json=meta,
                          headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                                   "X-Upload-Content-Length": str(size)})
    if start.status_code >= 400:
        raise SocialError(f"YouTube refused the upload ({start.status_code}): {start.text[:250]}")
    with open(video_path, "rb") as f:
        done = requests.put(start.headers["Location"], data=f, timeout=900,
                            headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if done.status_code >= 400:
        raise SocialError(f"YouTube upload failed ({done.status_code}): {done.text[:250]}")
    video_id = done.json()["id"]
    return video_id, f"https://www.youtube.com/shorts/{video_id}"


# ---------------------------------------------------------------- orchestration
def image_urls(listing):
    main = (listing or {}).get("main_image")
    return ([main] if main else []) + list((listing or {}).get("hosted_images") or [])


def post_draft(con, draft_id, platforms=None, retry=False, dry_run=False, use_llm=None):
    """Post one registered draft. Returns a list of {platform, status, ...}; never posts the same thing twice."""
    d = db.get_draft(con, draft_id)
    if d is None:
        raise SocialError(f"Draft {draft_id} does not exist.")
    listing = json.loads(d["listing"]) if d["listing"] else None
    if d["status"] != "published" or not listing:
        raise SocialError(f"Draft {draft_id} is {d['status']}; only drafts registered in the store are posted.")
    if is_paused(con):
        raise SocialError("Social posting is paused (python social.py --resume to turn it back on).")
    images = image_urls(listing)
    if not images:
        raise SocialError("This listing has no saved product image to post.")
    if use_llm is None:
        use_llm = os.getenv("SOCIAL_USE_LLM", "1") == "1"

    results, todo = [], []
    for platform in (platforms or enabled_platforms()):
        if not configured(platform):
            results.append({"platform": platform, "status": "skipped", "detail": "keys not set in .env"})
        elif not dry_run and state_of(con, draft_id, platform) in ("posted", "posting") :
            results.append({"platform": platform, "status": state_of(con, draft_id, platform), "detail": "already done"})
        elif not dry_run and state_of(con, draft_id, platform) == "failed" and not retry:
            results.append({"platform": platform, "status": "failed", "detail": "failed earlier; use retry"})
        else:
            todo.append(platform)
    if not todo and not dry_run:
        return results

    content = make_content(con, d, listing, use_llm)
    tags = json.loads(d["tags"] or "[]")
    video_path = OUT_DIR / f"draft-{draft_id}.mp4"

    if dry_run:
        plan = {"platform": "dry-run", "status": "preview", "caption": content["caption"],
                "yt_title": content["yt_title"], "caption_source": content["source"], "images": images,
                "would_post_to": todo}
        if "youtube" in todo or not todo:
            try:
                plan["video"] = str(make_video(images, video_path))
            except SocialError as e:
                plan["video_error"] = str(e)
        return results + [plan]

    for platform in todo:
        if not claim(con, draft_id, platform, retry):
            results.append({"platform": platform, "status": "skipped", "detail": "another run owns it"})
            continue
        try:
            if platform == "instagram":
                ext_id, url = post_instagram(content["caption"], images[0])
            else:
                video = make_video(images, video_path)
                description = content["caption"] + "\n\n#Shorts"
                ext_id, url = post_youtube(content["yt_title"], description, video, [t for t in tags])
            finish(con, draft_id, platform, "posted", url=url, external_id=ext_id, caption=content["caption"])
            results.append({"platform": platform, "status": "posted", "url": url})
        except Exception as e:
            finish(con, draft_id, platform, "failed", error=str(e)[:500], caption=content["caption"])
            results.append({"platform": platform, "status": "failed", "error": str(e)})
    return results


def start(draft_id, retry=False):
    """Run post_draft in the background. Never raises. The thread is not a daemon, so a command-line
    run waits for it instead of exiting in the middle of an upload."""
    def run():
        try:
            con = db.connect()
            post_draft(con, draft_id, retry=retry)
        except Exception as e:
            try:
                db.log_action(db.connect(), "social_error", f"draft_id={draft_id} {e}")
            except Exception:
                pass
    threading.Thread(target=run, name=f"social-{draft_id}").start()


def after_listing(draft_id):
    """Called by listing.register right after a draft is registered in the store."""
    if os.getenv("SOCIAL_AUTOPOST") == "1":
        start(draft_id)


# ---------------------------------------------------------------- one-time YouTube login
def youtube_login():
    """Open Google's consent page, catch the answer on localhost, print the refresh token for .env."""
    client_id, secret = os.getenv("YT_CLIENT_ID"), os.getenv("YT_CLIENT_SECRET")
    if not (client_id and secret):
        raise SystemExit("Put YT_CLIENT_ID and YT_CLIENT_SECRET in backend/.env first (see .env.social.example).")
    port = 8765
    redirect = f"http://localhost:{port}"
    got = {}

    class Catch(BaseHTTPRequestHandler):
        def do_GET(self):
            got.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"You can close this tab and go back to the terminal.")

        def log_message(self, *args):
            pass

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "access_type": "offline",
        "prompt": "consent", "scope": "https://www.googleapis.com/auth/youtube.upload"})
    print("Opening your browser. Sign in with the Google account that owns the YouTube channel.\nIf nothing opens, go to:\n" + url)
    webbrowser.open(url)
    server = HTTPServer(("127.0.0.1", port), Catch)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if "error" in got:
        raise SystemExit(f"Google answered: {got['error']}")
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": got["code"], "client_id": client_id, "client_secret": secret,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    token = r.json().get("refresh_token")
    if not token:
        raise SystemExit(f"No refresh token received: {r.text[:300]}")
    print("\nAdd this line to backend/.env (keep it secret):\n\nYT_REFRESH_TOKEN=" + token)


# ---------------------------------------------------------------- command line
def main():
    parser = argparse.ArgumentParser(description="Post registered products to Instagram and YouTube Shorts")
    parser.add_argument("draft_id", nargs="?", type=int)
    parser.add_argument("--dry-run", action="store_true", help="show the caption, render the video, post nothing")
    parser.add_argument("--retry", action="store_true", help="try again where an earlier attempt failed")
    parser.add_argument("--platform", choices=PLATFORMS, help="only this platform")
    parser.add_argument("--no-llm", action="store_true", help="use the formula caption (no tokens)")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--youtube-login", action="store_true")
    args = parser.parse_args()

    con = db.connect()
    if args.youtube_login:
        return youtube_login()
    if args.pause or args.resume:
        db.set_setting(con, "social_paused", "1" if args.pause else "0")
        db.log_action(con, "social_paused" if args.pause else "social_resumed", "by=cli")
        return print("Social posting is now " + ("PAUSED." if args.pause else "ON (when SOCIAL_AUTOPOST=1 or run by hand)."))
    if args.status or args.draft_id is None:
        print(f"paused: {is_paused(con)} | autopost: {os.getenv('SOCIAL_AUTOPOST') == '1'} | "
              f"configured: { {p: configured(p) for p in PLATFORMS} }")
        for r in con.execute("SELECT draft_id, platform, status, url, error FROM social_posts ORDER BY id DESC LIMIT 30"):
            print(f"  draft {r['draft_id']:>4} {r['platform']:<10} {r['status']:<8} {r['url'] or ''} {r['error'] or ''}")
        return
    try:
        results = post_draft(con, args.draft_id, [args.platform] if args.platform else None, retry=args.retry,
                             dry_run=args.dry_run, use_llm=False if args.no_llm else None)
    except SocialError as e:
        sys.exit(f"Not posted: {e}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
