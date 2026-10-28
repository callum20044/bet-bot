"""Upload the newest Footy in 30 video to YouTube (Data API v3, resumable upload).

Needs secrets YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (youtube.upload scope, from the OAuth Playground).
Until Google audits the app, YouTube keeps API uploads private - the video still lands on the channel and can be
switched to Public from the YouTube Studio app. Never prints secrets.

  python shorts/upload_youtube.py [path/to/video.mp4]   (default: newest video in shorts/out)
"""
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def env(k):
    return os.environ.get(k, "").strip().strip('"').strip("'")


def check():
    cid, sec, ref = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN")
    print(f"YT_CLIENT_ID: length {len(cid)} (looks right: {cid.endswith('.apps.googleusercontent.com')})")
    print(f"YT_CLIENT_SECRET: starts {sec[:7]!r}, length {len(sec)} (looks right: {sec.startswith('GOCSPX-')})")
    print(f"YT_REFRESH_TOKEN: starts {ref[:3]!r}, length {len(ref)} (looks right: {ref.startswith('1//')})")


def token():
    data = urllib.parse.urlencode({"client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
                                   "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data, timeout=30) as r:
        return json.load(r)["access_token"]


def upload(path):
    meta = json.load(open(path[:-4] + ".json")) if os.path.exists(path[:-4] + ".json") else {}
    title = (meta.get("title") or "Premier League in 30 seconds ⚽") + " #shorts"
    desc = (meta.get("description") or "") + "\n\nFollow @footyin30 for every weekend in 30 seconds."
    body = {"snippet": {"title": title[:100], "description": desc[:4900], "categoryId": "17",
                        "tags": ["premier league", "football", "epl", "premier league results", "footy", "shorts"]},
            "status": {"privacyStatus": os.environ.get("YT_PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    tok = token()
    size = os.path.getsize(path)
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    with urllib.request.urlopen(req, timeout=60) as r:
        where = r.headers["Location"]
    with open(path, "rb") as f:
        up = urllib.request.Request(where, data=f.read(), method="PUT",
                                    headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    with urllib.request.urlopen(up, timeout=600) as r:
        res = json.load(r)
    vid = res.get("id")
    status = res.get("status", {}).get("privacyStatus")
    print(f"uploaded: https://youtube.com/shorts/{vid}  (privacy: {status})")
    return vid, status


if __name__ == "__main__":
    if not all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
        print("YouTube secrets not set - skipping upload")
        sys.exit(0)
    vids = [sys.argv[1]] if len(sys.argv) > 1 else sorted(glob.glob(os.path.join(OUT, "pl_*.mp4")), key=os.path.getmtime)
    if not vids:
        print("no video made this run - nothing to upload")
        sys.exit(0)
    check()
    try:
        upload(vids[-1])
    except urllib.error.HTTPError as e:
        print(f"upload failed: HTTP {e.code} {e.read().decode(errors='replace')[:400]}")
        sys.exit(1)
