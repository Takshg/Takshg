#!/usr/bin/env python3
"""Generates the pixel-art profile SVGs in assets/ and the caption in README.md.

Data sources (all optional — falls back to sample data so it always renders):
  * GitHub GraphQL API (needs GH_TOKEN env var) -> commits, PRs, stars, streak, languages, recent commits
  * Open-Meteo (no key) -> current weather for the window

Usage:
  python generate.py                 # live data
  python generate.py --demo          # sample data, no network
  python generate.py --demo --weather snow --night
"""
import argparse, base64, io, json, math, os, random, re, urllib.request
from datetime import datetime, date, timedelta
from html import escape
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "profile.json")))
ASSETS = os.path.join(ROOT, "assets")

# ---------------------------------------------------------------- pixel font (5x7)
_F = """
A .###. #...# #...# ##### #...# #...# #...#
B ####. #...# #...# ####. #...# #...# ####.
C .###. #...# #.... #.... #.... #...# .###.
D ####. #...# #...# #...# #...# #...# ####.
E ##### #.... #.... ####. #.... #.... #####
F ##### #.... #.... ####. #.... #.... #....
G .###. #...# #.... #.### #...# #...# .####
H #...# #...# #...# ##### #...# #...# #...#
I .###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.
J ..### ...#. ...#. ...#. ...#. #..#. .##..
K #...# #..#. #.#.. ##... #.#.. #..#. #...#
L #.... #.... #.... #.... #.... #.... #####
M #...# ##.## #.#.# #.#.# #...# #...# #...#
N #...# #...# ##..# #.#.# #..## #...# #...#
O .###. #...# #...# #...# #...# #...# .###.
P ####. #...# #...# ####. #.... #.... #....
Q .###. #...# #...# #...# #.#.# #..#. .##.#
R ####. #...# #...# ####. #.#.. #..#. #...#
S .#### #.... #.... .###. ....# ....# ####.
T ##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
U #...# #...# #...# #...# #...# #...# .###.
V #...# #...# #...# #...# #...# .#.#. ..#..
W #...# #...# #...# #.#.# #.#.# #.#.# .#.#.
X #...# #...# .#.#. ..#.. .#.#. #...# #...#
Y #...# #...# .#.#. ..#.. ..#.. ..#.. ..#..
Z ##### ....# ...#. ..#.. .#... #.... #####
0 .###. #...# #..## #.#.# ##..# #...# .###.
1 ..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.
2 .###. #...# ....# ...#. ..#.. .#... #####
3 ####. ....# ....# .###. ....# ....# ####.
4 ...#. ..##. .#.#. #..#. ##### ...#. ...#.
5 ##### #.... ####. ....# ....# #...# .###.
6 .###. #.... #.... ####. #...# #...# .###.
7 ##### ....# ...#. ..#.. .#... .#... .#...
8 .###. #...# #...# .###. #...# #...# .###.
9 .###. #...# #...# .#### ....# ....# .###.
. ..... ..... ..... ..... ..... ..... ..#..
- ..... ..... ..... .###. ..... ..... .....
> #.... .#... ..#.. ...#. ..#.. .#... #....
: ..... ..#.. ..... ..... ..... ..#.. .....
/ ....# ...#. ...#. ..#.. .#... .#... #....
' ..#.. ..#.. ..... ..... ..... ..... .....
! ..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..
"""
FONT = {}
for line in _F.strip().splitlines():
    ch, *rows = line.split()
    FONT[ch] = rows
FONT[" "] = ["....."] * 7


def text_width(s, scale=1):
    return max(0, len(s) * 6 - 1) * scale


def bitmap(s, x, y, scale, fill, extra=""):
    d = []
    for i, ch in enumerate(s.upper()):
        rows = FONT.get(ch, FONT[" "])
        for ry, row in enumerate(rows):
            for cx, px in enumerate(row):
                if px == "#":
                    d.append(f"M{x + (i * 6 + cx) * scale} {y + ry * scale}h{scale}v{scale}h-{scale}z")
    return f'<path d="{"".join(d)}" fill="{fill}" {extra}/>'


def R(x, y, w, h, fill, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" {extra}/>'


# ---------------------------------------------------------------- data
SAMPLE = {
    "followers": 42, "repos": 18, "commits": 612, "prs": 37, "stars": 54, "streak": 19,
    "languages": [("Python", "#3572A5"), ("Jupyter Notebook", "#DA5B0B"), ("SQL", "#e38c00"),
                  ("R", "#198CE7"), ("JavaScript", "#f1e05a"), ("Shell", "#89e051")],
    "commits_list": [("breachpoint", "feat: add anomaly scoring"),
                     ("whisper-interp", "probe attention heads"),
                     ("breachpoint", "fix: leakage in split"),
                     ("torch-lib", "init: core tensor ops"),
                     ("whisper-interp", "plot layer saliency"),
                     ("dotfiles", "chore: tidy aliases"),
                     ("breachpoint", "docs: update README")],
    "avatar": None,
}


def fetch_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


QUERY = """query($login:String!){ user(login:$login){
  followers{totalCount}
  repositories(ownerAffiliations:OWNER, first:100, privacy:PUBLIC, orderBy:{field:PUSHED_AT,direction:DESC}){
    totalCount
    nodes{ name stargazerCount isFork
      languages(first:5, orderBy:{field:SIZE,direction:DESC}){ edges{ size node{name color} } }
      defaultBranchRef{ target{ ... on Commit{ history(first:4){ nodes{
        messageHeadline committedDate author{ user{ login } } } } } } } } }
  contributionsCollection{ totalCommitContributions totalPullRequestContributions
    contributionCalendar{ weeks{ contributionDays{ contributionCount date } } } } } }"""


def github_data(login, token):
    res = fetch_json("https://api.github.com/graphql",
                     data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
                     headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    u = res["data"]["user"]
    repos = u["repositories"]["nodes"]
    lang = {}
    commits = []
    for r in repos:
        if r["isFork"]:
            continue
        for e in r["languages"]["edges"]:
            n = e["node"]
            lang.setdefault(n["name"], [0, n["color"] or "#8b949e"])[0] += e["size"]
        ref = r.get("defaultBranchRef")
        for c in ((ref or {}).get("target") or {}).get("history", {}).get("nodes", []):
            who = ((c.get("author") or {}).get("user") or {}).get("login", "")
            if who.lower() == login.lower():
                commits.append((c["committedDate"], r["name"], c["messageHeadline"]))
    commits.sort(reverse=True)
    days = [d for w in u["contributionsCollection"]["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    best = cur = 0
    for d in days:
        cur = cur + 1 if d["contributionCount"] > 0 else 0
        best = max(best, cur)
    return {
        "followers": u["followers"]["totalCount"], "repos": u["repositories"]["totalCount"],
        "commits": u["contributionsCollection"]["totalCommitContributions"],
        "prs": u["contributionsCollection"]["totalPullRequestContributions"],
        "stars": sum(r["stargazerCount"] for r in repos), "streak": best,
        "languages": [(k, v[1]) for k, v in sorted(lang.items(), key=lambda kv: -kv[1][0])][:7],
        "commits_list": [(n, m) for _, n, m in commits[:8]] or SAMPLE["commits_list"],
    }


def avatar_png(login):
    from PIL import Image
    raw = urllib.request.urlopen(f"https://github.com/{login}.png?size=128", timeout=20).read()
    im = Image.open(io.BytesIO(raw)).convert("RGB").resize((20, 20), Image.BOX).resize((100, 100), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def weather_now(lat, lon, tz):
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,weather_code,is_day&timezone={tz}")
    cur = fetch_json(url)["current"]
    c = cur["weather_code"]
    kind = ("clear" if c == 0 else "cloudy" if c in (1, 2, 3) else "fog" if c in (45, 48)
            else "snow" if c in (71, 73, 75, 77, 85, 86) else "storm" if c >= 95 else "rain")
    return kind, not cur["is_day"], round(cur["temperature_2m"])


# ---------------------------------------------------------------- room scene
def room_svg(d, weather, night, now):
    rnd = random.Random(7)
    o = []
    W, H = 200, 100
    # wall + stripes
    o.append(R(0, 0, W, 74, "#1e2030"))
    for x in range(4, W, 10):
        o.append(R(x, 0, 1, 74, "#222436"))
    # floor
    o.append(R(0, 74, W, 26, "#3a2a24"))
    for y in (81, 88, 95):
        o.append(R(0, y, W, 1, "#2e211c"))
    for i, y in enumerate((74, 81, 88, 95)):
        for x in range(10 + (i % 2) * 17, W, 34):
            o.append(R(x, y, 1, 7, "#2e211c"))
    o.append(R(0, 72, W, 2, "#4b3529"))

    # ---------- window
    sky = {("clear", 0): "#5aa9e6", ("cloudy", 0): "#8098ad", ("fog", 0): "#9aa5b1", ("rain", 0): "#56657a",
           ("storm", 0): "#3d4757", ("snow", 0): "#a7b6c6"}.get((weather, 0), "#5aa9e6")
    if night:
        sky = "#0b1a3a" if weather == "clear" else "#18202f"
    o.append(R(9, 11, 40, 34, "#14151f"))
    o.append('<clipPath id="win"><rect x="11" y="13" width="36" height="30"/></clipPath>')
    o.append('<g clip-path="url(#win)">')
    o.append(R(11, 13, 36, 30, sky))
    if weather == "clear":
        if night:
            o.append(R(36, 16, 5, 5, "#f2e8cf") + R(38, 15, 4, 4, sky))  # crescent moon
            for i in range(9):
                sx, sy = rnd.randint(12, 45), rnd.randint(14, 41)
                o.append(R(sx, sy, 1, 1, "#ffffff", f'class="tw" style="animation-delay:-{rnd.random()*3:.2f}s"'))
        else:
            o.append(R(15, 16, 6, 6, "#ffd866") + R(14, 17, 8, 4, "#ffd866") + R(16, 15, 4, 8, "#ffd866"))
    if weather in ("cloudy", "rain", "storm", "snow", "fog"):
        cl = "#c9d3dd" if not night else "#3a4458"
        if weather in ("rain", "storm"):
            cl = "#7d8a99" if not night else "#2c3444"
        for cx, cy, dur, dl in ((14, 17, 26, 0), (30, 23, 34, 12)):
            o.append(f'<g class="drift" style="animation-duration:{dur}s;animation-delay:-{dl}s">'
                     + R(cx, cy, 10, 3, cl) + R(cx + 2, cy - 2, 5, 2, cl) + R(cx + 6, cy - 1, 3, 1, cl) + "</g>")
    if weather in ("rain", "storm"):
        for i in range(16):
            x = 12 + (i * 37) % 34
            o.append(R(x, 8, 1, 2, "#9ecbff", f'class="rain" style="animation-delay:-{rnd.random():.2f}s"'))
    if weather == "storm":
        o.append(R(11, 13, 36, 30, "#ffffff", 'class="flash"'))
    if weather == "snow":
        for i in range(18):
            x = 12 + (i * 29) % 34
            o.append(R(x, 10, 1, 1, "#ffffff", f'class="snow" style="animation-delay:-{rnd.random()*5:.2f}s;'
                                                    f'animation-duration:{4 + rnd.random()*2:.1f}s"'))
    if weather == "fog":
        for y in (20, 28, 36):
            o.append(R(11, y, 36, 3, "#d0d6dc", 'opacity=".35"'))
    o.append("</g>")
    o.append(R(28, 13, 2, 30, "#14151f") + R(11, 27, 36, 2, "#14151f"))
    o.append(R(7, 45, 44, 2, "#6e4b36"))

    # ---------- name plate
    name = CFG["display_name"].upper()
    tw = text_width(name)
    px = (W - tw) // 2 - 5
    o.append(R(px - 1, 3, tw + 12, 13, "#14151f") + R(px, 4, tw + 10, 11, "#f2e8cf"))
    o.append(bitmap(name, px + 5, 6, 1, "#1e2030"))

    # ---------- confusion-matrix poster
    o.append(R(52, 21, 17, 17, "#14151f"))
    shades = ["#1f6feb", "#0d2b55", "#0d2b55", "#11336a", "#1f6feb", "#0d2b55", "#0d2b55", "#163f80", "#1f6feb"]
    for i, c in enumerate(shades):
        o.append(R(54 + (i % 3) * 5, 23 + (i // 3) * 5, 4, 4, c))

    # ---------- clock (set to current local time, minute hand keeps turning)
    o.append(f'<circle cx="176" cy="15" r="7" fill="#14151f"/><circle cx="176" cy="15" r="6" fill="#f2e8cf"/>')
    ha = (now.hour % 12 + now.minute / 60) * 30
    ma = now.minute * 6
    o.append(f'<g transform="rotate({ha:.1f} 176 15)"><rect x="175.5" y="11.5" width="1" height="4" fill="#1e2030"/></g>')
    o.append(f'<g transform="rotate({ma:.1f} 176 15)"><g class="minute">'
             f'<rect x="175.5" y="10" width="1" height="5.5" fill="#c0392b"/></g></g>')

    # ---------- shelves
    for sy in (36, 54):
        o.append(R(148, sy, 44, 2, "#5a3c2b") + R(152, sy + 2, 1, 2, "#4b3529") + R(187, sy + 2, 1, 2, "#4b3529"))
    x = 151
    for i, (lname, color) in enumerate(d["languages"][:7]):
        h = 10 + (i * 7) % 5
        w = 4 if i % 3 else 5
        o.append(R(x, 36 - h, w, h, color) + R(x, 36 - h + 2, w, 1, "#00000033"))
        o.append(f'<title>{escape(lname)}</title>')
        x += w + 1
    o.append(R(x + 2, 33, 10, 3, "#8957e5"))  # a book lying flat
    tx = 152
    for _ in range(max(0, int(CFG.get("trophies", 1)))):
        o.append(R(tx, 46, 7, 4, "#e3b341") + R(tx - 1, 46, 1, 2, "#e3b341") + R(tx + 7, 46, 1, 2, "#e3b341")
                 + R(tx + 2, 50, 3, 2, "#c99a2e") + R(tx + 1, 52, 5, 2, "#c99a2e"))
        tx += 10
    o.append(R(168, 47, 10, 7, "#e6e6e6") + R(169, 46, 10, 7, "#f5f5f5")  # paper stack
             + "".join(R(171, 48 + k * 2, 6, 1, "#9aa4af") for k in range(3)))
    o.append(R(182, 50, 5, 4, "#ffd866") + R(184, 48, 3, 3, "#ffd866") + R(187, 49, 1, 1, "#f08a24")
             + R(185, 49, 1, 1, "#14151f"))  # rubber duck (for debugging)

    # ---------- desk & stuff
    o.append('<linearGradient id="glow" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd866" '
             'stop-opacity="%s"/><stop offset="1" stop-color="#ffd866" stop-opacity="0"/></linearGradient>'
             '<polygon points="71,42 77,42 84,54 64,54" fill="url(#glow)"/>' % (".35" if night else ".18"))
    o.append(R(60, 54, 82, 3, "#6e4b36") + R(60, 54, 82, 1, "#85604a"))
    o.append(R(62, 57, 3, 16, "#5a3c2b") + R(137, 57, 3, 16, "#5a3c2b"))
    # lamp
    o.append(R(62, 52, 7, 2, "#2a2c3b") + R(65, 41, 1, 11, "#2a2c3b") + R(65, 40, 7, 1, "#2a2c3b")
             + R(70, 38, 8, 4, "#f2c14e") + R(71, 37, 6, 1, "#f2c14e"))
    # main monitor
    o.append(R(80, 24, 36, 26, "#2a2c3b") + R(82, 26, 32, 22, "#0d0f16"))
    o.append(R(96, 50, 4, 3, "#2a2c3b") + R(92, 53, 12, 1, "#2a2c3b"))
    lines = [("$", "git log --oneline")] + [(r, m) for r, m in d["commits_list"]]
    lh = 3.2
    o.append('<clipPath id="scr"><rect x="82" y="26" width="32" height="22"/></clipPath>')
    o.append(f'<g clip-path="url(#scr)"><g class="scroll" style="animation-duration:{len(lines) * 2.2:.1f}s">')
    for rep in range(2):
        for i, (repo, msg) in enumerate(lines):
            y = 29.5 + (rep * len(lines) + i) * lh
            if repo == "$":
                o.append(f'<text x="83" y="{y:.1f}" class="t" fill="#c9d1d9">$ {escape(msg)}</text>')
            else:
                head = repo[:10]
                rest = msg[: max(0, 21 - len(head))]
                o.append(f'<text x="83" y="{y:.1f}" class="t"><tspan fill="#7ee787">{escape(head)}:</tspan>'
                         f'<tspan fill="#c9d1d9"> {escape(rest)}</tspan></text>')
    o.append("</g></g>")
    o.append(R(83, 45.3, 2, 1, "#c9d1d9", 'class="blink"'))
    # second monitor: training loss
    o.append(R(120, 34, 18, 16, "#2a2c3b") + R(121, 35, 16, 14, "#0d0f16"))
    o.append(R(128, 50, 2, 4, "#2a2c3b") + R(125, 53, 8, 1, "#2a2c3b"))
    pts = []
    for i in range(15):
        xv = 122 + i
        yv = 37 + 9 * (1 - math.exp(-i / 3.5)) + rnd.uniform(-0.6, 0.6)
        pts.append(f"{xv},{min(47.5, yv):.1f}")
    o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#f2c14e" stroke-width=".7" '
             f'pathLength="100" class="loss"/>')
    o.append(R(122, 48, 14, 0.4, "#30363d"))
    o.append(f'<text x="122" y="38.4" class="t" style="font-size:1.8px" fill="#6e7681">loss</text>')
    # keyboard + mug + steam
    o.append(R(86, 52, 22, 2, "#3a3d4d") + "".join(R(87 + k * 2, 52.4, 1, .6, "#565a6e") for k in range(10)))
    o.append(R(110, 50, 4, 4, "#d9d9d9") + R(114, 51, 1, 2, "#d9d9d9"))
    for k, dl in enumerate((0, 1.2)):
        o.append(R(111 + k * 1.5, 47, 1, 2, "#c9d1d9", f'class="steam" style="animation-delay:-{dl}s"'))
    # chair
    o.append(R(92, 56, 12, 7, "#33364a") + R(90, 63, 16, 2, "#3a3d4d") + R(97, 65, 2, 7, "#2a2c3b")
             + R(91, 72, 14, 1, "#2a2c3b"))

    # ---------- plant
    o.append('<g class="sway">')
    o.append(R(26, 50, 1, 14, "#2d7a39"))
    for lx, ly, lw in ((21, 52, 5), (27, 55, 5), (20, 58, 6), (27, 60, 5), (24, 48, 4)):
        o.append(R(lx, ly, lw, 2, "#3fa34d") + R(lx, ly + 1, lw, 1, "#2d7a39"))
    o.append("</g>")
    o.append(R(20, 64, 13, 2, "#b5653a") + R(21, 66, 11, 8, "#a0522d"))

    # ---------- sleeping dog
    fur, dark, light = "#c58b4e", "#9a6532", "#e2b27c"
    o.append('<g class="breathe">' + R(149, 68, 16, 6, fur) + R(151, 67, 11, 1, fur)
             + R(153, 69, 5, 3, dark) + R(149, 73, 16, 1, dark) + "</g>")
    o.append(R(144, 71, 5, 2, fur) + R(143, 70, 2, 2, fur))                      # tail
    o.append(R(163, 66, 8, 7, fur) + R(170, 69, 5, 4, light) + R(174, 69, 1, 2, "#14151f")  # head, snout, nose
             + R(164, 66, 3, 6, dark) + R(167, 69, 2, 1, "#4b2e16")                      # floppy ear, closed eye
             + R(166, 73, 8, 1, light))                                                    # front paws
    o.append('<g class="zz">' + bitmap("Z", 175, 59, 0.6, "#8b949e") + "</g>")
    o.append(f'<g style="font-family:monospace"><text x="196" y="98" text-anchor="end" '
             f'style="font-size:2px" fill="#4b4e58">updated {now:%b %d %H:%M}</text></g>')

    css = """
    .t{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:2.2px}
    .scroll{animation:scroll linear infinite}
    @keyframes scroll{to{transform:translateY(-%(sh).1fpx)}}
    .blink{animation:blink 1s steps(1) infinite}@keyframes blink{50%%{opacity:0}}
    .tw{animation:tw 3s ease-in-out infinite}@keyframes tw{50%%{opacity:.2}}
    .drift{animation:drift linear infinite}@keyframes drift{from{transform:translateX(-30px)}to{transform:translateX(40px)}}
    .rain{animation:rain .7s linear infinite}@keyframes rain{to{transform:translateY(36px)}}
    .snow{animation:snow linear infinite}@keyframes snow{50%%{transform:translate(1px,17px)}to{transform:translate(-1px,34px)}}
    .flash{opacity:0;animation:flash 7s infinite}@keyframes flash{0%%,92%%,96%%,100%%{opacity:0}93%%,97%%{opacity:.7}}
    .minute{animation:spin 3600s linear infinite;transform-origin:176px 15px}@keyframes spin{to{transform:rotate(360deg)}}
    .loss{stroke-dasharray:100;animation:loss 7s ease-out infinite}
    @keyframes loss{0%%{stroke-dashoffset:100}70%%,100%%{stroke-dashoffset:0}}
    .steam{animation:steam 2.4s ease-out infinite}@keyframes steam{0%%{opacity:0;transform:translateY(2px)}30%%{opacity:.6}100%%{opacity:0;transform:translateY(-4px)}}
    .sway{animation:sway 6s ease-in-out infinite;transform-origin:26px 64px}@keyframes sway{50%%{transform:rotate(2deg)}}
    .breathe{animation:breathe 3s ease-in-out infinite;transform-origin:157px 74px}@keyframes breathe{50%%{transform:scaleY(1.12)}}
    .zz{animation:zz 3s ease-out infinite}@keyframes zz{0%%{opacity:0;transform:translate(0,3px)}30%%{opacity:1}100%%{opacity:0;transform:translate(3px,-4px)}}
    @media (prefers-reduced-motion:reduce){*{animation:none!important}}
    """ % {"sh": len(lines) * lh}
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="800" height="400" '
            f'shape-rendering="crispEdges" role="img" aria-label="Pixel-art room of {escape(CFG["display_name"])}">'
            f'<style>{css}</style><clipPath id="card"><rect width="{W}" height="{H}" rx="3"/></clipPath>'
            f'<g clip-path="url(#card)">{"".join(o)}</g></svg>')


# ---------------------------------------------------------------- cards
CARD_BG, CARD_BORDER = "#161b22", "#30363d"
SANS = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


def card(w, h, inner):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="10" fill="{CARD_BG}" stroke="{CARD_BORDER}"/>'
            f'{inner}</svg>')


def about_svg(d):
    name = CFG["display_name"].upper()
    inner = [bitmap("> ABOUT", 32, 30, 2, "#7ee787", 'shape-rendering="crispEdges"'),
             bitmap(name, 32, 62, 4, "#f0f6fc", 'shape-rendering="crispEdges"'),
             f'<text x="32" y="122" font-family="{SANS}" font-size="16" fill="#c9d1d9">{escape(CFG["tagline"])}</text>',
             f'<text x="32" y="150" font-family="{MONO}" font-size="12.5" fill="#8b949e">'
             f'{escape(CFG["location"])} · {escape(CFG["status"])} · {d["followers"]} followers · {d["repos"]} repositories</text>']
    if d.get("avatar"):
        inner.append(f'<rect x="657" y="37" width="110" height="110" fill="#f0f6fc"/>'
                     f'<image x="662" y="42" width="100" height="100" style="image-rendering:pixelated" '
                     f'href="data:image/png;base64,{d["avatar"]}"/>')
    else:
        initials = "".join(p[0] for p in CFG["display_name"].split()[:2]).upper()
        inner.append(f'<rect x="657" y="37" width="110" height="110" fill="#f0f6fc"/>'
                     f'<rect x="662" y="42" width="100" height="100" fill="#1f6feb"/>'
                     + bitmap(initials, 712 - text_width(initials, 5) // 2, 75, 5, "#f0f6fc", 'shape-rendering="crispEdges"'))
    return card(800, 184, "".join(inner))


def short(n):
    return f"{n/1000:.1f}K".replace(".0K", "K") if n >= 1000 else str(n)


def stats_svg(d):
    items = [(short(d["commits"]), "COMMITS"), (str(d["prs"]), "PULL REQS"),
             (short(d["stars"]), "STARS"), (f'{d["streak"]}D', "BEST STREAK")]
    inner = []
    for i, (v, label) in enumerate(items):
        x = 32 + i * 192
        inner.append(bitmap(v, x, 22, 4, "#f0f6fc", 'shape-rendering="crispEdges"'))
        inner.append(bitmap(label, x, 62, 2, "#8b949e", 'shape-rendering="crispEdges"'))
    return card(800, 96, "".join(inner))


def header_svg(title):
    w = text_width("> " + title, 3)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 34" width="800" height="34">'
            + bitmap("> " + title, 2, 6, 3, "#7ee787", 'shape-rendering="crispEdges"')
            + f'<line x1="{w + 18}" y1="17" x2="798" y2="17" stroke="#30363d" stroke-width="2" stroke-dasharray="6 6"/></svg>')


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--weather", choices=["clear", "cloudy", "fog", "rain", "storm", "snow"])
    ap.add_argument("--night", action="store_true")
    a = ap.parse_args()

    tz = ZoneInfo(CFG["timezone"])
    now = datetime.now(tz)
    data = dict(SAMPLE)
    weather, night, temp = "clear", not (7 <= now.hour < 18), None
    if not a.demo:
        token = os.environ.get("GH_TOKEN")
        try:
            if token:
                data.update(github_data(CFG["username"], token))
        except Exception as e:
            print("GitHub fetch failed, using sample data:", e)
        try:
            data["avatar"] = avatar_png(CFG["username"])
        except Exception as e:
            print("Avatar fetch failed:", e)
        try:
            weather, night, temp = weather_now(CFG["latitude"], CFG["longitude"], CFG["timezone"])
        except Exception as e:
            print("Weather fetch failed:", e)
    if a.weather:
        weather = a.weather
    if a.night:
        night = True

    os.makedirs(ASSETS, exist_ok=True)
    out = {"room.svg": room_svg(data, weather, night, now), "about.svg": about_svg(data),
           "stats.svg": stats_svg(data), "projects.svg": header_svg("PROJECTS"), "toolkit.svg": header_svg("TOOLKIT")}
    for fn, svg in out.items():
        open(os.path.join(ASSETS, fn), "w").write(svg)

    city = CFG["location"].split(",")[0]
    wtxt = {"clear": "clear skies", "cloudy": "clouds", "fog": "fog", "rain": "rain",
            "storm": "a thunderstorm", "snow": "snow"}[weather]
    temp_txt = f", {temp}°C" if temp is not None else ""
    caption = (f"The window shows the weather in {city} right now ({wtxt}{temp_txt}). "
               f"The main monitor scrolls my latest commits, the second one is always training something, "
               f"and the shelf holds a book for each language I write.")
    readme = os.path.join(ROOT, "README.md")
    txt = open(readme).read()
    txt = re.sub(r"(<!-- CAPTION:START -->).*?(<!-- CAPTION:END -->)",
                 lambda m: f"{m.group(1)}\n<sub>{caption}</sub>\n{m.group(2)}", txt, flags=re.S)
    open(readme, "w").write(txt)
    print("done:", weather, "night" if night else "day", f"{temp}°C" if temp is not None else "")


if __name__ == "__main__":
    main()
