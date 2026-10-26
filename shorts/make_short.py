"""Automated football stats Shorts: "Premier League weekend in 30 seconds".

  python shorts/make_short.py            -> real data (ESPN), last 4 days of Premier League results
  python shorts/make_short.py --demo     -> made-up data, for testing the look offline

Pipeline: results + table from ESPN's public JSON -> a handful of stat slides -> British voice-over with
edge-tts (free Microsoft voices; silent video if it's unavailable) -> 1080x1920 frames with Pillow ->
MP4 with ffmpeg. Text and colours only: no club badges, logos or footage (those belong to others).
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
VOICE = "en-GB-RyanNeural"
BG_TOP, BG_BOT = (12, 18, 38), (38, 12, 64)
ACCENT, WHITE, GREY, GREEN, RED = (255, 196, 0), (245, 245, 250), (160, 165, 185), (80, 220, 140), (255, 90, 90)


def font(size, bold=True):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ------------------------------------------------------------------ data
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def fetch(days=4, league="eng.1"):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    events, seen = [], set()
    for i in range(days + 1):
        day = start + timedelta(days=i)
        try:
            sb = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={day:%Y%m%d}")
        except Exception as e:
            print(f"  {day:%Y-%m-%d}: {e}")
            continue
        for ev in sb.get("events", []):
            if ev["id"] not in seen:
                seen.add(ev["id"])
                events.append(ev)
    games = []
    for ev in events:
        comp = ev["competitions"][0]
        if not comp.get("status", {}).get("type", {}).get("completed"):
            continue
        t = {c["homeAway"]: c for c in comp["competitors"]}
        games.append(dict(home=t["home"]["team"]["shortDisplayName"], away=t["away"]["team"]["shortDisplayName"],
                          hg=int(t["home"]["score"]), ag=int(t["away"]["score"])))
    st = get(f"https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings")
    table = []
    for e in st["children"][0]["standings"]["entries"]:
        s = {x["name"]: x.get("value") for x in e["stats"]}
        table.append(dict(team=e["team"]["shortDisplayName"], pts=int(s.get("points", 0)), gd=int(s.get("pointDifferential", 0)),
                          played=int(s.get("gamesPlayed", 0)), rank=int(s.get("rank", 0) or 0)))
    table.sort(key=lambda r: (r["rank"] or 99, -r["pts"]))
    return games, table


def demo():
    games = [dict(home="Arsenal", away="Wolves", hg=4, ag=0), dict(home="Man City", away="Spurs", hg=2, ag=2),
             dict(home="Liverpool", away="Everton", hg=3, ag=1), dict(home="Chelsea", away="Brighton", hg=1, ag=2),
             dict(home="Newcastle", away="Fulham", hg=0, ag=0), dict(home="Aston Villa", away="Brentford", hg=3, ag=3),
             dict(home="Man United", away="West Ham", hg=2, ag=1), dict(home="Bournemouth", away="Leeds", hg=1, ag=0),
             dict(home="Burnley", away="Sunderland", hg=0, ag=1), dict(home="Crystal Palace", away="Nott'm Forest", hg=2, ag=0)]
    names = ["Arsenal", "Liverpool", "Man City", "Chelsea", "Aston Villa", "Newcastle", "Man United", "Brighton", "Spurs",
             "Bournemouth", "Crystal Palace", "Fulham", "Brentford", "West Ham", "Everton", "Nott'm Forest", "Leeds",
             "Sunderland", "Wolves", "Burnley"]
    table = [dict(team=n, pts=25 - i, gd=12 - i, played=9, rank=i + 1) for i, n in enumerate(names)]
    return games, table


# ------------------------------------------------------------------ story
def slides(games, table, title="PREMIER LEAGUE"):
    if not games:
        raise SystemExit("no finished games in the window")
    goals = sum(g["hg"] + g["ag"] for g in games)
    big = max(games, key=lambda g: (abs(g["hg"] - g["ag"]), g["hg"] + g["ag"]))
    most = max(games, key=lambda g: (g["hg"] + g["ag"], -abs(g["hg"] - g["ag"])))
    draws = [g for g in games if g["hg"] == g["ag"]]
    away_wins = [g for g in games if g["ag"] > g["hg"]]

    def score(g):
        return f"{g['home']} {g['hg']}-{g['ag']} {g['away']}"

    def winner(g):
        return (g["home"], g["away"]) if g["hg"] > g["ag"] else (g["away"], g["home"])

    out = [dict(kind="title", big=title, small="THE WEEKEND IN 30 SECONDS",
                say=f"The Premier League weekend in thirty seconds. {len(games)} games, {goals} goals.")]
    w, l = winner(big)
    out.append(dict(kind="stat", label="BIGGEST WIN", big=f"{big['hg']}-{big['ag']}", small=f"{big['home']} v {big['away']}",
                    say=f"Biggest win: {w} beat {l} {max(big['hg'], big['ag'])} {min(big['hg'], big['ag'])}."))
    if most is not big:
        out.append(dict(kind="stat", label="GOAL FEST", big=f"{most['hg'] + most['ag']} GOALS", small=score(most),
                        say=f"Most goals: {most['home']} {most['hg']}, {most['away']} {most['ag']}."))
    out.append(dict(kind="stat", label="BY THE NUMBERS", big=f"{goals / len(games):.1f}", small="goals per game",
                    say=f"{goals / len(games):.1f} goals a game, {len(draws)} draw{'s' if len(draws) != 1 else ''} "
                        f"and {len(away_wins)} away win{'s' if len(away_wins) != 1 else ''}."))
    out.append(dict(kind="results", label="ALL THE RESULTS", rows=[score(g) for g in games][:10],
                    say="Here are all the results."))
    top = table[:3]
    out.append(dict(kind="table", label="TOP OF THE TABLE", rows=[(r["rank"], r["team"], r["pts"]) for r in top],
                    say=f"{top[0]['team']} top the table on {top[0]['pts']} points"
                        + (f", {top[0]['pts'] - top[1]['pts']} clear of {top[1]['team']}." if top[0]['pts'] > top[1]['pts']
                           else f", level with {top[1]['team']}.")))
    bot = table[-3:]
    out.append(dict(kind="table", label="DROP ZONE", rows=[(r["rank"], r["team"], r["pts"]) for r in bot], danger=True,
                    say=f"At the bottom, {bot[-1]['team']} are on {bot[-1]['pts']} points."))
    out.append(dict(kind="title", big="FOLLOW", small="FOR EVERY WEEKEND IN 30 SECONDS", say="Follow for every weekend in thirty seconds."))
    return out


# ------------------------------------------------------------------ drawing
def background():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))
    lines = img.copy()
    dl = ImageDraw.Draw(lines)
    for i in range(-H, W, 140):                       # faint diagonal stripes
        dl.line([(i, H), (i + H, 0)], fill=(255, 255, 255), width=3)
    return Image.blend(img, lines, 0.04)


BG = None


def centred(d, y, text, f, fill, max_w=W - 120):
    while d.textlength(text, font=f) > max_w and f.size > 30:
        f = font(f.size - 4)
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def draw(slide, t, idx, n):
    """t in [0,1]: intro animation progress."""
    global BG
    BG = BG or background()
    img = BG.copy()
    d = ImageDraw.Draw(img)
    ease = 1 - (1 - min(t, 1)) ** 3
    off = int((1 - ease) * 120)
    a = lambda c: tuple(int(x * ease + y * (1 - ease)) for x, y in zip(c, BG_TOP))    # fade-in colour
    # progress dots
    for i in range(n):
        x = W / 2 + (i - (n - 1) / 2) * 34
        d.ellipse([x - 7, 110, x + 7, 124], fill=ACCENT if i <= idx else (70, 70, 100))
    k = slide["kind"]
    if k == "title":
        centred(d, 700 + off, slide["big"], font(120), a(WHITE))
        d.rectangle([W / 2 - 160, 860 + off, W / 2 + 160, 868 + off], fill=a(ACCENT))
        centred(d, 900 + off, slide["small"], font(52), a(ACCENT))
    elif k == "stat":
        centred(d, 520, slide["label"], font(60), a(ACCENT))
        centred(d, 720 + off, slide["big"], font(230), a(WHITE))
        centred(d, 1040 + off, slide["small"], font(58, bold=False), a(GREY))
    elif k == "results":
        centred(d, 300, slide["label"], font(64), a(ACCENT))
        for i, row in enumerate(slide["rows"]):
            yy = 470 + i * 120 + int(off * (1 + i * 0.3))
            d.rounded_rectangle([60, yy - 14, W - 60, yy + 86], radius=24, fill=(255, 255, 255, 20) if False else (40, 36, 80))
            centred(d, yy + 8, row, font(50), a(WHITE), max_w=W - 170)
    elif k == "table":
        centred(d, 520, slide["label"], font(70), a(RED if slide.get("danger") else ACCENT))
        for i, (rank, team, pts) in enumerate(slide["rows"]):
            yy = 720 + i * 190 + off
            d.rounded_rectangle([80, yy, W - 80, yy + 150], radius=30, fill=(40, 36, 80))
            d.text((130, yy + 40), f"{rank}", font=font(70), fill=a(RED if slide.get("danger") else ACCENT))
            tf = font(64)
            while d.textlength(team, font=tf) > 560:
                tf = font(tf.size - 4)
            d.text((260, yy + 42), team, font=tf, fill=a(WHITE))
            p = f"{pts} pts"
            d.text((W - 130 - d.textlength(p, font=font(60)), yy + 44), p, font=font(60), fill=a(GREEN))
    d.text((60, H - 110), "stats in 30 seconds", font=font(36, bold=False), fill=(120, 120, 150))
    return img


# ------------------------------------------------------------------ audio
async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+8%").save(path)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def build(sl, name):
    work = os.path.join(OUT, "work")
    os.makedirs(work, exist_ok=True)
    concat, audio_parts, voiced = [], [], True
    for i, s in enumerate(sl):
        ap = os.path.join(work, f"a{i}.mp3")
        try:
            asyncio.run(_tts(s["say"], ap))
            dur = duration(ap) + 0.45
        except Exception as e:
            if voiced:
                print(f"no voice-over ({e.__class__.__name__}: {e}) - making a silent video")
            voiced, dur = False, max(2.2, len(s["say"]) / 15)
        audio_parts.append((ap if voiced else None, dur))
        intro = 12
        for f in range(intro):
            p = os.path.join(work, f"s{i}_{f}.png")
            draw(s, (f + 1) / intro, i, len(sl)).save(p)
            concat.append(f"file '{p}'\nduration {1 / FPS:.5f}")
        p = os.path.join(work, f"s{i}_hold.png")
        draw(s, 1, i, len(sl)).save(p)
        concat.append(f"file '{p}'\nduration {max(dur - intro / FPS, 0.3):.3f}")
    concat.append(f"file '{p}'")
    open(os.path.join(work, "frames.txt"), "w").write("\n".join(concat) + "\n")
    video = os.path.join(work, "video.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "frames.txt"),
                    "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20", video], check=True)
    final = os.path.join(OUT, f"{name}.mp4")
    if voiced:
        # pad each clip to its slide length, then join
        parts = []
        for i, (ap, dur) in enumerate(audio_parts):
            pp = os.path.join(work, f"p{i}.wav")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ap, "-af", f"apad=whole_dur={dur:.3f}", "-ar", "44100", "-ac", "2", pp], check=True)
            parts.append(f"file '{pp}'")
        open(os.path.join(work, "audio.txt"), "w").write("\n".join(parts) + "\n")
        audio = os.path.join(work, "audio.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "audio.txt"), audio], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", audio, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-shortest", final], check=True)
    else:
        os.replace(video, final)
    meta = dict(title=f"Premier League weekend in 30 seconds ⚽ {datetime.now(timezone.utc):%d %b %Y}",
                description="Every Premier League result and the key stats from the weekend, in 30 seconds.\n"
                            "#PremierLeague #football #shorts",
                script=[s["say"] for s in sl], voiced=voiced, length=round(sum(d for _, d in audio_parts), 1))
    json.dump(meta, open(os.path.join(OUT, f"{name}.json"), "w"), indent=1, ensure_ascii=False)
    print(f"made {final} ({meta['length']}s, voice-over {'yes' if voiced else 'no'})")
    return final


if __name__ == "__main__":
    games, table = demo() if "--demo" in sys.argv else fetch()
    build(slides(games, table), ("demo" if "--demo" in sys.argv else f"pl_{datetime.now(timezone.utc):%Y%m%d}"))
