"""Automated football stats Shorts: "Premier League weekend in 30 seconds" (v2, 26 Oct).

  python shorts/make_short.py            -> real data (ESPN), last 4 days of Premier League results
  python shorts/make_short.py --demo     -> made-up data, for testing the look offline

Pipeline: results + table + team colours from ESPN's public JSON -> stat slides -> British voice-over
(edge-tts; silent if unavailable) -> animated 1080x1920 frames (moving colour background in the teams'
colours, team-colour chips, pop-in text) piped into ffmpeg -> home-made background beat mixed under the voice.
No club crests/logos or footage: teams are shown as chips in their colours with their short code.
"""
import asyncio
import json
import math
import os
import random
import subprocess
import sys
import urllib.request
import wave
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
VOICE, RATE = "en-GB-RyanNeural", "+16%"
CHANNEL = os.environ.get("SHORTS_CHANNEL", "@footyin30")
ACCENT, WHITE, GREY, GREEN, RED = (255, 204, 0), (248, 248, 252), (200, 204, 220), (90, 235, 150), (255, 95, 95)
BRAND = [(255, 0, 110), (131, 56, 236), (58, 134, 255), (0, 200, 170), (255, 190, 11)]
TEAMS = {}       # short name -> dict(abbr, color, alt)


def font(size, bold=True):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def hexrgb(h, default=(90, 90, 120)):
    try:
        h = (h or "").strip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def lum(c):
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255


# ------------------------------------------------------------------ data
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def _team(t):
    name = t.get("shortDisplayName") or t.get("displayName")
    TEAMS.setdefault(name, {})
    info = TEAMS[name]
    info["abbr"] = t.get("abbreviation") or info.get("abbr") or name[:3].upper()
    if t.get("color"):
        info["color"], info["alt"] = hexrgb(t.get("color")), hexrgb(t.get("alternateColor"), (255, 255, 255))
    return name


def fetch(days=3, league="eng.1"):
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
        games.append(dict(home=_team(t["home"]["team"]), away=_team(t["away"]["team"]),
                          hg=int(t["home"]["score"]), ag=int(t["away"]["score"])))
    st = get(f"https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings")
    table = []
    for e in st["children"][0]["standings"]["entries"]:
        s = {x["name"]: x.get("value") for x in e["stats"]}
        table.append(dict(team=_team(e["team"]), pts=int(s.get("points", 0)), gd=int(s.get("pointDifferential", 0)),
                          played=int(s.get("gamesPlayed", 0)), rank=int(s.get("rank", 0) or 0)))
    table.sort(key=lambda r: (r["rank"] or 99, -r["pts"]))
    return games, table


def demo():
    cols = {"Arsenal": ("ARS", "EF0107"), "Liverpool": ("LIV", "C8102E"), "Man City": ("MNC", "6CABDD"), "Chelsea": ("CHE", "034694"),
            "Aston Villa": ("AVL", "670E36"), "Newcastle": ("NEW", "241F20"), "Man United": ("MAN", "DA291C"), "Brighton": ("BHA", "0057B8"),
            "Spurs": ("TOT", "132257"), "Bournemouth": ("BOU", "DA291C"), "Crystal Palace": ("CRY", "1B458F"), "Fulham": ("FUL", "000000"),
            "Brentford": ("BRE", "E30613"), "West Ham": ("WHU", "7A263A"), "Everton": ("EVE", "003399"), "Nott'm Forest": ("NFO", "DD0000"),
            "Leeds": ("LEE", "FFCD00"), "Sunderland": ("SUN", "EB172B"), "Wolves": ("WOL", "FDB913"), "Burnley": ("BUR", "6C1D45")}
    for n, (a, c) in cols.items():
        TEAMS[n] = dict(abbr=a, color=hexrgb(c), alt=(255, 255, 255))
    games = [dict(home="Arsenal", away="Wolves", hg=4, ag=0), dict(home="Man City", away="Spurs", hg=2, ag=2),
             dict(home="Liverpool", away="Everton", hg=3, ag=1), dict(home="Chelsea", away="Brighton", hg=1, ag=2),
             dict(home="Newcastle", away="Fulham", hg=0, ag=0), dict(home="Aston Villa", away="Brentford", hg=3, ag=3),
             dict(home="Man United", away="West Ham", hg=2, ag=1), dict(home="Bournemouth", away="Leeds", hg=1, ag=0),
             dict(home="Burnley", away="Sunderland", hg=0, ag=1), dict(home="Crystal Palace", away="Nott'm Forest", hg=2, ag=0)]
    table = [dict(team=n, pts=25 - i, gd=12 - i, played=9, rank=i + 1) for i, n in enumerate(cols)]
    return games, table


def colour(team):
    c = TEAMS.get(team, {}).get("color")
    if not c or lum(c) < 0.12:                       # black/very dark kits: use the second colour so it shows
        c = TEAMS.get(team, {}).get("alt") or (120, 120, 140)
        if lum(c) > 0.9:
            c = (110, 110, 130)
    return c


# ------------------------------------------------------------------ story
def slides(games, table, title="PREMIER LEAGUE"):
    if len(games) < 3:
        print(f"only {len(games)} finished games in the window - no video this time")
        sys.exit(0)
    goals = sum(g["hg"] + g["ag"] for g in games)
    big = max(games, key=lambda g: (abs(g["hg"] - g["ag"]), g["hg"] + g["ag"]))
    most = max(games, key=lambda g: (g["hg"] + g["ag"], -abs(g["hg"] - g["ag"])))
    draws = [g for g in games if g["hg"] == g["ag"]]
    away_wins = [g for g in games if g["ag"] > g["hg"]]
    win, lose = (big["home"], big["away"]) if big["hg"] >= big["ag"] else (big["away"], big["home"])
    out = [dict(kind="title", big=title, small="THE WEEKEND IN 30 SECONDS", teams=[],
                say=f"The Premier League weekend in thirty seconds. {len(games)} games, {goals} goals.")]
    out.append(dict(kind="match", label="BIGGEST WIN", game=big, teams=[big["home"], big["away"]],
                    say=f"Biggest win: {win} beat {lose} {max(big['hg'], big['ag'])} {min(big['hg'], big['ag'])}."))
    if most is not big:
        out.append(dict(kind="match", label=f"GOAL FEST: {most['hg'] + most['ag']} GOALS", game=most, teams=[most["home"], most["away"]],
                        say=f"Most goals: {most['home']} {most['hg']}, {most['away']} {most['ag']}."))
    out.append(dict(kind="stat", label="BY THE NUMBERS", big=f"{goals / len(games):.1f}", small="goals per game", teams=[],
                    extra=[(str(len(draws)), "draws"), (str(len(away_wins)), "away wins")],
                    say=f"{goals / len(games):.1f} goals a game, {len(draws)} draw{'s' if len(draws) != 1 else ''} "
                        f"and {len(away_wins)} away win{'s' if len(away_wins) != 1 else ''}."))
    out.append(dict(kind="results", label="ALL THE RESULTS", rows=games[:10], teams=[g["home"] for g in games[:4]],
                    say="Here are all the results."))
    top = table[:3]
    out.append(dict(kind="table", label="TOP OF THE TABLE", rows=top, teams=[r["team"] for r in top],
                    say=f"{top[0]['team']} top the table on {top[0]['pts']} points"
                        + (f", {top[0]['pts'] - top[1]['pts']} clear of {top[1]['team']}." if top[0]['pts'] > top[1]['pts']
                           else f", level with {top[1]['team']}.")))
    bot = table[-3:]
    out.append(dict(kind="table", label="DROP ZONE", rows=bot, danger=True, teams=[r["team"] for r in bot],
                    say=f"At the bottom, {bot[-1]['team']} are on {bot[-1]['pts']} points."))
    out.append(dict(kind="title", big="FOLLOW", small=f"{CHANNEL.upper()} FOR EVERY WEEKEND", teams=[],
                    say="Follow Footy in 30 for every weekend in thirty seconds."))
    return out


# ------------------------------------------------------------------ drawing
PAD = 260


def make_bg(cols, seed):
    """Big dark canvas with soft blurred colour blobs; frames crop a slowly drifting window from it."""
    rnd = random.Random(seed)
    bw, bh = W + 2 * PAD, H + 2 * PAD
    base = Image.new("RGB", (bw, bh))
    d = ImageDraw.Draw(base)
    for y in range(bh):
        t = y / bh
        d.line([(0, y), (bw, y)], fill=(int(10 + 14 * t), int(10 + 4 * t), int(28 + 24 * t)))
    small = Image.new("RGB", (bw // 4, bh // 4), (0, 0, 0))
    ds = ImageDraw.Draw(small)
    cols = (cols or []) + rnd.sample(BRAND, 3)
    for i, c in enumerate(cols[:5]):
        r = rnd.randint(70, 130)
        x, y = rnd.randint(0, bw // 4), rnd.randint(0, bh // 4)
        ds.ellipse([x - r, y - r, x + r, y + r], fill=c)
    blobs = small.filter(ImageFilter.GaussianBlur(45)).resize((bw, bh), Image.BICUBIC)
    img = Image.blend(base, blobs, 0.55)
    # subtle diagonal light streaks
    streak = Image.new("L", (bw, bh), 0)
    dl = ImageDraw.Draw(streak)
    for i in range(-bh, bw, 180):
        dl.line([(i, bh), (i + bh, 0)], fill=255, width=40)
    streak = streak.filter(ImageFilter.GaussianBlur(30))
    return Image.composite(Image.new("RGB", (bw, bh), (255, 255, 255)), img, streak.point(lambda v: v * 0.05))


def fit(d, text, size, max_w, bold=True):
    f = font(size, bold)
    while d.textlength(text, font=f) > max_w and size > 24:
        size -= 3
        f = font(size, bold)
    return f


def centred(d, y, text, size, fill, max_w=W - 120, bold=True, stroke=0):
    f = fit(d, text, size, max_w, bold)
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def chip(d, cx, cy, r, team, alpha=255):
    c = colour(team)
    ring = TEAMS.get(team, {}).get("alt") or (255, 255, 255)
    if abs(lum(ring) - lum(c)) < 0.15:
        ring = (255, 255, 255) if lum(c) < 0.6 else (20, 20, 30)
    d.ellipse([cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6], fill=ring + (alpha,))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c + (alpha,))
    txt = TEAMS.get(team, {}).get("abbr", team[:3].upper())
    f = font(int(r * 0.62))
    tc = (255, 255, 255) if lum(c) < 0.6 else (15, 15, 25)
    d.text((cx - d.textlength(txt, font=f) / 2, cy - r * 0.38), txt, font=f, fill=tc + (alpha,))


def card(d, box, alpha=150, radius=34):
    d.rounded_rectangle(box, radius=radius, fill=(8, 8, 20, int(alpha)))


def layer(s, t, idx, n):
    """Transparent text/graphics layer for one slide; t in [0,1] = intro progress (pop-in)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    e = 1 - (1 - min(t, 1)) ** 3
    back = 1 + 0.25 * math.sin(min(t, 1) * math.pi) * (1 - e)      # tiny overshoot
    off = int((1 - e) * 140)
    A = int(255 * min(1, t * 1.6))
    a = lambda c: c + (A,)
    for i in range(n):                                               # progress bar
        x0 = 80 + i * ((W - 160) / n)
        d.rounded_rectangle([x0 + 4, 96, x0 + (W - 160) / n - 4, 106], radius=5, fill=ACCENT + (255,) if i <= idx else (255, 255, 255, 60))
    k = s["kind"]
    if k == "title":
        card(d, [70, 640 + off, W - 70, 1110 + off], 120 * e)
        centred(d, 720 + off, s["big"], int(118 * back), a(WHITE), stroke=2)
        d.rounded_rectangle([W / 2 - 170, 880 + off, W / 2 + 170, 892 + off], radius=6, fill=a(ACCENT))
        centred(d, 930 + off, s["small"], 50, a(ACCENT))
    elif k == "match":
        g = s["game"]
        centred(d, 430, s["label"], 66, a(ACCENT), stroke=2)
        card(d, [60, 600 + off, W - 60, 1320 + off], 150 * e)
        chip(d, 270, 800 + off, int(120 * back), g["home"], A)
        chip(d, W - 270, 800 + off, int(120 * back), g["away"], A)
        centred(d, 960 + off, f"{g['hg']}  -  {g['ag']}", int(190 * back), a(WHITE), stroke=3)
        centred(d, 1200 + off, f"{g['home']}  v  {g['away']}", 50, a(GREY), bold=False)
    elif k == "stat":
        centred(d, 430, s["label"], 66, a(ACCENT), stroke=2)
        card(d, [90, 600 + off, W - 90, 1080 + off], 150 * e)
        centred(d, 640 + off, s["big"], int(250 * back), a(WHITE), stroke=3)
        centred(d, 950 + off, s["small"], 58, a(GREY), bold=False)
        for i, (num, lab) in enumerate(s.get("extra", [])):
            x0 = 120 + i * 440
            yy = 1160 + int(off * 1.5)
            card(d, [x0, yy, x0 + 400, yy + 230], 150 * e)
            f = font(110)
            d.text((x0 + 200 - d.textlength(num, font=f) / 2, yy + 20), num, font=f, fill=a(ACCENT))
            f2 = font(44, bold=False)
            d.text((x0 + 200 - d.textlength(lab, font=f2) / 2, yy + 150), lab, font=f2, fill=a(GREY))
    elif k == "results":
        centred(d, 230, s["label"], 66, a(ACCENT), stroke=2)
        for i, g in enumerate(s["rows"]):
            yy = 380 + i * 132 + int(off * (1 + i * 0.35))
            card(d, [50, yy, W - 50, yy + 112], 165 * e, 30)
            chip(d, 115, yy + 56, 40, g["home"], A)
            chip(d, W - 115, yy + 56, 40, g["away"], A)
            sc = f"{g['hg']} - {g['ag']}"
            f = font(56)
            d.text(((W - d.textlength(sc, font=f)) / 2, yy + 22), sc, font=f, fill=a(WHITE))
            for name, left in ((g["home"], True), (g["away"], False)):
                fn = fit(d, name, 38, 250)
                x = 175 if left else W - 175 - d.textlength(name, font=fn)
                won = (g["hg"] > g["ag"]) if left else (g["ag"] > g["hg"])
                d.text((x, yy + 34), name, font=fn, fill=a(WHITE if won else GREY))
    elif k == "table":
        col = RED if s.get("danger") else ACCENT
        centred(d, 470, s["label"], 76, a(col), stroke=2)
        for i, r in enumerate(s["rows"]):
            yy = 680 + i * 210 + int(off * (1 + i * 0.4))
            card(d, [70, yy, W - 70, yy + 170], 165 * e)
            d.text((115, yy + 45), f"{r['rank']}", font=font(76), fill=a(col))
            chip(d, 300, yy + 85, 58, r["team"], A)
            fn = fit(d, r["team"], 60, 400)
            d.text((385, yy + 50), r["team"], font=fn, fill=a(WHITE))
            p = f"{r['pts']}"
            d.text((W - 130 - d.textlength(p, font=font(76)), yy + 28), p, font=font(76), fill=a(GREEN))
            d.text((W - 130 - d.textlength("pts", font=font(30, False)), yy + 118), "pts", font=font(30, False), fill=a(GREY))
    tag = CHANNEL
    d.text((60, H - 120), tag, font=font(40), fill=(255, 255, 255, 150))
    return img


# ------------------------------------------------------------------ audio
async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def beat(path, seconds, bpm=100, sr=44100):
    """Home-made, copyright-free backing track: kick, clap, hats and a soft chord pad."""
    n = int(seconds * sr)
    out = np.zeros(n)
    spb = 60 / bpm
    t = np.arange(int(0.35 * sr)) / sr
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-t * 25)) * t) * np.exp(-t * 9)
    rng = np.random.default_rng(1)
    clap = rng.normal(0, 1, int(0.18 * sr)) * np.exp(-np.arange(int(0.18 * sr)) / sr * 22)
    hat = rng.normal(0, 1, int(0.05 * sr)) * np.exp(-np.arange(int(0.05 * sr)) / sr * 90)
    hat = np.diff(hat, prepend=0)                     # brighter
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]   # Am F C G
    beats = int(seconds / spb) + 1
    for b in range(beats):
        s = int(b * spb * sr)
        def add(x, at, g):
            e = min(n, at + len(x))
            if at < n:
                out[at:e] += g * x[:e - at]
        if b % 2 == 0:
            add(kick, s, 0.9)
        else:
            add(clap, s, 0.25)
        add(hat, s + int(spb * sr / 2), 0.12)
        add(hat, s, 0.06)
        if b % 4 == 0:
            ln = int(4 * spb * sr)
            tt = np.arange(ln) / sr
            env = np.minimum(1, tt / 0.4) * np.minimum(1, (4 * spb - tt) / 0.6)
            pad = sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * tt) for m in chords[(b // 4) % 4]) / 3
            add(pad * env, s, 0.18)
    fade = int(1.5 * sr)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = out / (np.abs(out).max() or 1) * 0.6
    pcm = (np.repeat(out[:, None], 2, axis=1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def build(sl, name):
    work = os.path.join(OUT, "work")
    os.makedirs(work, exist_ok=True)
    clips, voiced = [], True
    for i, s in enumerate(sl):
        ap = os.path.join(work, f"a{i}.mp3")
        try:
            asyncio.run(_tts(s["say"], ap))
            dur = duration(ap) + 0.35
        except Exception as e:
            if voiced:
                print(f"no voice-over ({e.__class__.__name__}: {e}) - making it without a voice")
            voiced, dur = False, max(2.2, len(s["say"]) / 16)
        if s["kind"] == "results":
            dur = max(dur, 4.5)                     # long enough to read ten scores
        clips.append((ap if voiced else None, dur))
    total = sum(d for _, d in clips)
    video = os.path.join(work, "video.mp4")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
                           "-pix_fmt", "yuv420p", video], stdin=subprocess.PIPE)
    frame_no = 0
    for i, (s, (_, dur)) in enumerate(zip(sl, clips)):
        cols = [colour(t) for t in s.get("teams", [])]
        bg = make_bg(cols, seed=i * 7 + 3)
        hold = layer(s, 1, i, len(sl))
        intro = 14
        for f in range(int(round(dur * FPS))):
            g = frame_no / FPS
            x = PAD + int(PAD * 0.8 * math.sin(g * 0.35 + i))
            y = PAD + int(PAD * 0.8 * math.cos(g * 0.27 + i * 1.7))
            frame = bg.crop((x, y, x + W, y + H)).convert("RGBA")
            lay = layer(s, (f + 1) / intro, i, len(sl)) if f < intro else hold
            ff.stdin.write(Image.alpha_composite(frame, lay).convert("RGB").tobytes())
            frame_no += 1
    ff.stdin.close()
    ff.wait()
    final = os.path.join(OUT, f"{name}.mp4")
    music = os.path.join(work, "beat.wav")
    beat(music, total + 0.5)
    if voiced:
        parts = []
        for i, (ap, dur) in enumerate(clips):
            pp = os.path.join(work, f"p{i}.wav")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ap, "-af", f"apad=whole_dur={dur:.3f}", "-ar", "44100", "-ac", "2", pp], check=True)
            parts.append(f"file '{pp}'")
        open(os.path.join(work, "audio.txt"), "w").write("\n".join(parts) + "\n")
        voice = os.path.join(work, "voice.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "audio.txt"), voice], check=True)
        mix = "[1:a]volume=1.0[v];[2:a]volume=0.22[m];[v][m]amix=inputs=2:duration=first:normalize=0[a]"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", voice, "-i", music, "-filter_complex", mix,
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", final], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", music, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", final], check=True)
    meta = dict(title=f"Premier League weekend in 30 seconds ⚽ {datetime.now(timezone.utc):%d %b %Y}",
                description="Every Premier League result and the key stats from the weekend, in 30 seconds.\n"
                            "#premierleague #football #footy #footballtiktok #pl #epl #shorts",
                script=[s["say"] for s in sl], voiced=voiced, length=round(total, 1))
    json.dump(meta, open(os.path.join(OUT, f"{name}.json"), "w"), indent=1, ensure_ascii=False)
    print(f"made {final} ({meta['length']}s, voice-over {'yes' if voiced else 'no'})")
    return final


if __name__ == "__main__":
    games, table = demo() if "--demo" in sys.argv else fetch()
    build(slides(games, table), ("demo" if "--demo" in sys.argv else f"pl_{datetime.now(timezone.utc):%Y%m%d}"))
