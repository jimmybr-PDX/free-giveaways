#!/usr/bin/env python3
"""Near-zero-cost sweepstakes finder for Jimmy (Oregon City, OR). Stdlib only.
Read-only: fetches public listings, never enters or submits anything.
Usage: python3 find_sweeps.py [--verbose]
"""
import csv, html, os, re, sys, time, json, urllib.request, urllib.robotparser, urllib.parse
from datetime import date, datetime
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "tracker.csv"); HTML_PATH = os.path.join(HERE, "tracker.html")
CACHE_PATH = os.path.join(HERE, "detail_cache.json")
DETAIL_CAP = 30
AGG_RX = r"sweepstakesfanatics\.com|online-sweepstakes\.com|contestgirl\.com"
STATUS_PATH = os.path.join(HERE, "last_run_status.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) SweepsFinder/1.0 (personal, low-volume)"
COLS = ["prize","est_value","entry_link","end_date","frequency","entered_date","won","first_seen","source","tag"]
TODAY = date.today()
VERBOSE = "--verbose" in sys.argv

# (name, url, kind)
SOURCES = [
    ("Sweepstakes Fanatics", "https://sweepstakesfanatics.com/feed/", "rss"),
    ("Sweepstakes Fanatics p2", "https://sweepstakesfanatics.com/feed/?paged=2", "rss"),
    ("Online-Sweepstakes", "https://online-sweepstakes.com/feed/", "rss"),
    ("Online-Sweepstakes p2", "https://online-sweepstakes.com/feed/?paged=2", "rss"),
    ("Contestgirl daily", "https://www.contestgirl.com/contests/contests.pl?f=d&c=us&b=nb&sort=p&ar=na&s=_", "contestgirl"),
    ("Contestgirl single", "https://www.contestgirl.com/contests/contests.pl?f=s&c=us&b=nb&sort=p&ar=na&s=_", "contestgirl"),
    ("Sweepstakes Advantage", "https://www.sweepstakesadvantage.com/rss.xml", "rss"),  # robots disallows all -> skipped
    ("Golf Digest", "https://www.golfdigest.com/sweepstakes", "page"),
    ("PGA Tour", "https://www.pgatour.com/sweepstakes", "page"),
    ("TaylorMade", "https://www.taylormadegolf.com/sweepstakes", "page"),
    ("Callaway", "https://www.callawaygolf.com/sweepstakes", "page"),
    ("Titleist", "https://www.titleist.com/sweepstakes", "page"),
    ("Corona Golf", "https://www.coronausa.com/pages/golf-2026", "page"),
    ("LPGA Ford", "https://www.lpga.com/ford-tee-to-trail-sweepstakes", "page"),
    # Local Portland / Oregon City / Clackamas (filtered to free/giveaway posts, tagged Local)
    ("Reddit r/portland", "https://www.reddit.com/r/portland/search.rss?q=free+OR+giveaway&restrict_sr=1&sort=new", "local"),
    ("Reddit r/askportland", "https://www.reddit.com/r/askportland/search.rss?q=free+OR+giveaway&restrict_sr=1&sort=new", "local"),
    ("Patch Oregon City", "https://patch.com/feeds/aol/oregon/oregoncity", "local"),
    ("Patch Lake Oswego", "https://patch.com/feeds/aol/oregon/lakeoswego", "local"),
    ("KGW local", "https://www.kgw.com/feeds/syndication/rss/news/local", "local"),
    ("OregonLive", "https://www.oregonlive.com/arc/outboundfeeds/rss/?outputType=xml", "local"),
    ("KOIN", "https://www.koin.com/feed/", "local"),
    ("KPTV", "https://www.kptv.com/arc/outboundfeeds/rss/?outputType=xml", "local"),
    ("Willamette Week", "https://www.wweek.com/arc/outboundfeeds/rss/?outputType=xml", "local"),
]
LOCAL_RX = r"\bfree (food|admission|tickets?|event|day|museum|entry|stuff|meal|coffee|concert|movie|parking|rides?|classes?|flu shots?|covid|tree|trees|compost|mulch)|freebies?|giveaways?|give away|giving away|sweepstakes|chance to win|\bwin (a|free|tickets)|free things to do|for free\b|\bfree\b.*\b(portland|oregon city|clackamas|milwaukie|lake oswego|west linn|gladstone)"
LOCAL_NOISE = r"live stream|how to watch|\bwatch\b.*\b(live|tonight|game)\b|betting|best bets|odds|predictions|picks|winners|medals|free agen|free throw|free-for-all|scam"

_robots = {}
def allowed(url):
    p = urllib.parse.urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            rp.parse([] if e.code >= 400 and e.code != 401 and e.code != 403 else ["User-agent: *", "Disallow: /"])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")

def clean(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def tag(block, t):
    m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", block, re.S)
    return m.group(1) if m else ""

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
def parse_end(text):
    pats = [rf"(?:ends?|end date|expires?|deadline|through|until|closes?|ending)[^.]{{0,40}}?(({MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}})",
            r"(?:ends?|end date|expires?|deadline|through|until|closes?)[^.]{0,40}?(\d{1,2}/\d{1,2}/\d{2,4})"]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            s = m.group(1).replace(",", "").replace(".", "").replace("Sept", "Sep")
            for f in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
                try: return datetime.strptime(s, f).date()
                except ValueError: pass
    return None

def parse_value(text):
    vals = []
    for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|K|million|Million|M\b)?", text):
        try: v = float(m.group(1).replace(",", ""))
        except ValueError: continue
        suf = (m.group(2) or "").lower()
        v *= 1000 if suf == "k" else 1_000_000 if suf in ("m", "million") else 1
        vals.append(v)
    return int(max(vals)) if vals else 0

STATES = ["Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut","Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa","Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan","Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada","New Hampshire","New Jersey","New Mexico","New York","North Carolina","North Dakota","Ohio","Oklahoma","Oregon","Pennsylvania","Rhode Island","South Carolina","South Dakota","Tennessee","Texas","Utah","Vermont","Virginia","Washington","West Virginia","Wisconsin","Wyoming"]

def reject_reason(title, text):
    t = (title + " " + text); tl = t.lower()
    if re.search(r"purchase (is )?(necessary|required)|purchase required|requires? (a )?purchase|with (any |qualifying )?purchase|receipt upload|upload (your |a )?receipt|entry fee|pay to enter", tl) and "no purchase necessary" not in tl:
        return "purchase"
    if re.search(r"limited states|select states", tl) and "oregon" not in tl:
        return "OR-not-eligible"
    m2 = re.search(r"\b((?:[A-Z]{2}(?:,| and| &|\s)\s*)+)Only\b", title)
    if m2 and "OR" not in re.findall(r"[A-Z]{2}", m2.group(1)):
        return "OR-not-eligible"
    if re.search(r"\bpurchase\b", title, re.I):
        return "purchase"
    if re.search(r"\bsurvey", tl) and re.search(r"points|panel|paid|rewards|earn", tl):
        return "survey"
    if re.search(r"canad(a|ian) (residents )?only|open (only )?to (legal )?residents of (canada|the uk|united kingdom|australia)|\buk only\b|australia only", tl):
        return "non-US"
    if re.search(r"(excluding|excludes|except|void in|not open to (residents of)?)[^.]{0,200}\b(oregon|OR\b)", t, re.I):
        return "OR-excluded"
    m = re.search(r"open only to (legal )?(u\.?s\.? )?residents (residing )?(of|in) ([^.]{0,600})", t, re.I)
    if m:
        seg = m.group(5)
        named = [s for s in STATES if s in seg]
        if len(named) >= 1 and "Oregon" not in named and not re.search(r"50 (united )?states|fifty|the united states|the u\.?s\.?|D\.?C\.", seg[:80], re.I):
            return "OR-not-eligible"
    if re.search(r"\b(19|20|21|25)\+? years|age of (19|20|21|25)|\b21\+|legal drinking age", tl):
        pass  # Jimmy is an adult; 21+ assumed OK
    return None

def freq(text):
    return "daily" if re.search(r"\bdaily\b|once per day|once a day|every day|1x/day", text, re.I) else "once"

SCORE_KW = [
    (60, r"golf trip|golf getaway|pebble beach|st\.? andrews|pinehurst|bandon|masters|ryder cup|pga (championship|tour)|tee time|tee-to-trail|\bgolf\b"),
    (45, r"golf clubs?|driver|irons|golf balls?|putter|taylormade|callaway|titleist|ping|golf bag"),
    (55, r"\b(car|truck|vehicle|suv|ford|toyota|chevy|chevrolet|jeep|tesla|bronco|f-150|ranger)\b"),
    (40, r"cash|\$\d[\d,]* (cash|check|visa)|visa gift card|paypal"),
    (35, r"iphone|ipad|macbook|laptop|playstation|ps5|xbox|nintendo|switch 2|tv\b|television|oled|camera|drone|gaming pc|apple watch|airpods|projector|speaker"),
    (25, r"gift card|watch|rolex|e-?bike|bike|grill|traeger|yeti|generator|kayak"),
    (20, r"trip|vacation|getaway|cruise|travel"),
]
def score(title, text, value):
    t = (title + " " + text).lower()
    s = sum(w for w, p in SCORE_KW if re.search(p, t))
    if value: s += min(80, int(value ** 0.5))
    if re.search(r"sample|coupon|recipe|e-?book|sticker|digital download", t): s -= 25
    return s

def parse_rss(src, body):
    out = []
    for it in re.findall(r"<item>.*?</item>", body, re.S):
        title = clean(tag(it, "title")); link = clean(tag(it, "link"))
        txt = clean(tag(it, "description")) + " " + clean(tag(it, "content:encoded")) + " " + " ".join(clean(c) for c in re.findall(r"<category>(.*?)</category>", it, re.S))
        if not title or not link: continue
        out.append(dict(title=title, link=link, text=txt, source=src))
    return out

def parse_local(src, body):
    out = []
    for it in parse_rss(src, body) + parse_atom(src, body):
        if re.search(LOCAL_RX, it["title"], re.I) and not re.search(LOCAL_NOISE, it["title"], re.I):
            it["tag"] = "Local"; out.append(it)
    return out

def parse_atom(src, body):
    out = []
    for e in re.findall(r"<entry>.*?</entry>", body, re.S):
        m = re.search(r'<link[^>]*href="([^"]+)"', e)
        title = clean(tag(e, "title"))
        if title and m: out.append(dict(title=title, link=html.unescape(m.group(1)), text=clean(tag(e, "content")), source=src))
    return out

def decode_cg(chu):
    return chu.replace("!!2", "a").replace("!!3", "e").replace("!!5", "o")

def parse_contestgirl(src, body):
    out = []
    for row in re.split(r'<tr bgcolor="#(?:eeeeee|fcfcfc)"', body)[1:]:
        m = re.search(r'sweepstakesDetail\.pl\?index=(\d+)', row)
        t = re.search(r'--><a href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not (m and t): continue
        detail = f"https://www.contestgirl.com/contests/sweepstakesDetail.pl?index={m.group(1)}"
        title = clean(t.group(2)).replace("--", " - ")
        txt = clean(row)
        out.append(dict(title=title, link=detail, text=txt, source=src))
    return out

def parse_page(src, url, body):
    title = clean(re.search(r"<title[^>]*>(.*?)</title>", body, re.S|re.I).group(1)) if re.search(r"<title", body, re.I) else src
    desc = ""
    for p in (r'<meta[^>]+(?:name|property)="(?:og:)?description"[^>]+content="([^"]*)"',):
        m = re.search(p, body, re.I)
        if m: desc = html.unescape(m.group(1))
    txt = desc + " " + clean(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S|re.I))[:20000]
    if not re.search(r"sweepstakes|giveaway|enter to win|chance to win", txt, re.I): return []
    return [dict(title=title, link=url, text=txt, source=src)]

def article_text(url, body):
    """Return (text, official_url) for an aggregator detail page."""
    nos = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    official = ""
    if "contestgirl.com" in url:
        txt = clean(nos); i = txt.find(" Prize:")
        txt = txt[i if i > 0 else 0:]; txt = txt.split("Comments:")[0]
        # contestgirl's outbound redirect (/sweepstakes/) is robots-disallowed; keep detail page
    else:
        i = nos.find("entry-content")
        seg = nos[i:i + 60000] if i > 0 else nos
        txt = clean(seg)[:8000]
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', seg, re.S):
            label = clean(m.group(2))
            if re.search(r"click here to enter|enter now|enter here|official (entry|site)", label, re.I) and not re.search(AGG_RX, m.group(1)):
                official = html.unescape(m.group(1)); break
    return txt, official

def parse_detail(url, body):
    txt, official = article_text(url, body)
    end = parse_end(txt)
    if not end:
        m = re.search(rf"End(?:s| Date)?:?\s*(({MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}})", txt)
        if m: end = parse_end("ends " + m.group(1))
    fm = re.search(r"(?:Entry Frequency|Entry Limit|Frequency):\s*([A-Za-z ]{3,30})", txt)
    fword = (fm.group(1) if fm else "").lower()
    fr = "daily" if re.search(r"daily|unlimited|per day", fword) else ("once" if fword else freq(txt))
    em = re.search(r"(Eligib\w*|Restrictions):\s*(.{0,700}?)(?:Start Date|End Date|Sponsor|Entry |Value:|Frequency|Prize|$)", txt)
    elig = em.group(2) if em else ""
    why = None
    if end and end < TODAY: why = "expired"
    else: why = reject_reason("", elig + " " + txt[:3000])
    return dict(end_date=end.isoformat() if end else "", frequency=fr, official=official, eligibility=elig[:300], reject=why or "")

_cache = None
def detail(url, budget):
    """Cached detail lookup. budget is a one-item list counting remaining fetches."""
    global _cache
    if _cache is None:
        _cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
    if url in _cache: return _cache[url]
    if budget[0] <= 0 or not re.search(AGG_RX, url): return None
    budget[0] -= 1
    try:
        if not allowed(url): res = dict(error="robots")
        else: res = parse_detail(url, fetch(url))
    except Exception as e:
        res = dict(error=f"{type(e).__name__}")
    time.sleep(1)
    res["checked"] = TODAY.isoformat(); _cache[url] = res
    return res

def load_rows():
    if not os.path.exists(CSV_PATH): return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = [{c: (r.get(c) or "") for c in COLS} for r in csv.DictReader(f)]
    for r in rows: r["tag"] = r["tag"] or "Sweeps"  # migrate older rows; user columns untouched
    return rows

def norm_title(t): return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
def norm_url(u): return u.split("#")[0].rstrip("/").lower().replace("http://", "https://").replace("://www.", "://")

def main():
    rows = load_rows()
    seen_urls = {norm_url(r["entry_link"]) for r in rows}
    seen_titles = {norm_title(r["prize"]) for r in rows}
    status, cands = {}, []
    for name, url, kind in SOURCES:
        try:
            if not allowed(url): status[name] = "SKIPPED (robots.txt disallows)"; continue
            body = fetch(url)
            items = parse_rss(name, body) if kind == "rss" else parse_contestgirl(name, body) if kind == "contestgirl" else parse_local(name, body) if kind == "local" else parse_page(name, url, body)
            status[name] = f"OK ({len(items)} items)"; cands += items
        except urllib.error.HTTPError as e: status[name] = f"FAILED (HTTP {e.code})"
        except Exception as e: status[name] = f"FAILED ({type(e).__name__}: {str(e)[:60]})"
        time.sleep(1)
    global _cache
    _cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
    budget = [DETAIL_CAP]
    new, rejected = [], {}
    for c in cands:
        nu, nt = norm_url(c["link"]), norm_title(c["title"])
        if nu in seen_urls or nt in seen_titles or c["link"] in (_cache or {}): continue
        seen_urls.add(nu); seen_titles.add(nt)
        end = parse_end(c["text"])
        if end and end < TODAY: rejected["expired"] = rejected.get("expired", 0) + 1; continue
        why = reject_reason(c["title"], c["text"])
        if why: rejected[why] = rejected.get(why, 0) + 1; continue
        d = detail(c["link"], budget)
        if d and not d.get("error"):
            if d["reject"]: rejected["detail:" + d["reject"]] = rejected.get("detail:" + d["reject"], 0) + 1; continue
            if d["end_date"]: end = date.fromisoformat(d["end_date"])
            c["dfreq"] = d["frequency"]
            if d["official"]: c["agg"] = c["link"]; c["link"] = d["official"]
        val = parse_value(c["title"] + " " + c["text"][:3000])
        c.update(end=end.isoformat() if end else "", value=val, freq=c.get("dfreq") or freq(c["text"]), score=score(c["title"], c["text"][:3000], val))
        new.append(c)
    new.sort(key=lambda c: -c["score"])
    # re-validate existing rows missing an end date (capped; continues on later runs)
    reval = {"checked": 0, "excluded": {}, "dated": 0}
    for r in rows:
        if r["end_date"] or r["tag"] == "Excluded" or not re.search(AGG_RX, r["entry_link"]): continue
        if r["entry_link"] in (_cache or {}) or budget[0] <= 0: continue
        d = detail(r["entry_link"], budget)
        if not d or d.get("error"): continue
        reval["checked"] += 1
        if d["end_date"]: r["end_date"] = d["end_date"]; reval["dated"] += 1
        if d["frequency"]: r["frequency"] = d["frequency"]
        if d["reject"]:
            r["tag"] = "Excluded"; reval["excluded"][d["reject"]] = reval["excluded"].get(d["reject"], 0) + 1
        elif d["official"]: r["entry_link"] = d["official"]
    stamp = TODAY.isoformat()
    for c in new:
        rows.append(dict(prize=c["title"], est_value=c["value"] or "", entry_link=c["link"], end_date=c["end"],
                         frequency=c["freq"], entered_date="", won="", first_seen=stamp, source=c["source"], tag=c.get("tag", "Sweeps")))
    # append-only: existing rows (incl. user-filled entered_date/won) are written back untouched
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    os.replace(tmp, CSV_PATH)
    if _cache is not None: json.dump(_cache, open(CACHE_PATH, "w"), indent=0)
    write_html(rows)
    json.dump({"date": stamp, "sources": status, "new": len(new), "rejected": rejected, "revalidated": reval, "detail_fetches": DETAIL_CAP - budget[0]}, open(STATUS_PATH, "w"), indent=1)
    if VERBOSE:
        for k, v in status.items(): print(f"# {k}: {v}", file=sys.stderr)
        print(f"# new={len(new)} rejected={rejected} reval={reval} fetches={DETAIL_CAP - budget[0]}", file=sys.stderr)
    if not new: print("NOTHING_NEW"); return
    for i, c in enumerate(new[:10], 1):
        v = f"${c['value']:,}" if c["value"] else "?"
        print(f"{i}. {c['title']} | {v} | ends {c['end'] or '?'} | {c['link']}")

def write_html(rows):
    def kw_score(r):
        try: v = int(float(r["est_value"] or 0))
        except ValueError: v = 0
        return score(r["prize"], "", v)
    data = []
    for r in rows:
        d = dict(r); d["score"] = kw_score(r)
        data.append(d)
    js = json.dumps(data).replace("</", "<\\/")
    page = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sweeps Tracker</title><style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:8px;background:#f6f7f9}
h1{font-size:20px;margin:6px 0}.bar{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}
button,select,input{font-size:15px;padding:8px;border-radius:8px;border:1px solid #ccc;background:#fff}
.card{background:#fff;border-radius:10px;padding:10px;margin:8px 0;box-shadow:0 1px 3px #0002}
.card a{font-size:17px;font-weight:600;color:#0645ad;text-decoration:none;display:block;padding:4px 0}
.meta{font-size:13px;color:#555}.done{opacity:.55}.card a.go{display:block;text-align:center;background:#0a7d3b;color:#fff;border-radius:8px;padding:12px;margin:6px 0;font-size:16px}.loc{background:#ffe8b3}.tag{display:inline-block;background:#e8f0fe;border-radius:6px;padding:1px 6px;margin-right:4px}
</style></head><body><h1>Sweeps Tracker <small class="meta">updated """ + TODAY.isoformat() + """</small></h1>
<div class="bar"><select id="s"><option value="new">Newest + value</option><option value="score">Best first</option><option value="value">Value</option><option value="end">Ending soon</option><option value="prize">A-Z</option></select>
<input id="q" placeholder="filter" size="10"><select id="t"><option value="">All</option><option>Local</option><option>Sweeps</option></select><label class="meta"><input type="checkbox" id="h"> hide entered</label><label class="meta"><input type="checkbox" id="x"> show expired</label></div><div id="l"></div>
<script>const D=""" + js + """;
function v(x){return parseFloat(x.est_value)||0}
function r(){const T=new Date().toISOString().slice(0,10),tg=document.getElementById('t').value,sx=document.getElementById('x').checked,s=document.getElementById('s').value,q=document.getElementById('q').value.toLowerCase(),h=document.getElementById('h').checked;
let a=D.filter(x=>(!q||(x.prize+x.source).toLowerCase().includes(q))&&!(h&&x.entered_date&&x.frequency!='daily')&&(sx||!x.end_date||x.end_date>=T)&&(!tg||x.tag==tg)&&x.tag!='Excluded');
a.sort((x,y)=>s=='value'?v(y)-v(x):s=='end'?((x.end_date||'9999')<(y.end_date||'9999')?-1:1):s=='new'?(y.first_seen.localeCompare(x.first_seen)||v(y)-v(x)):s=='prize'?x.prize.localeCompare(y.prize):y.score-x.score);
const e=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('l').innerHTML=a.map(x=>`<div class="card ${x.entered_date?'done':''}"><a href="${e(x.entry_link)}" target="_blank" rel="noopener">${e(x.prize)}</a><a class="go" href="${e(x.entry_link)}" target="_blank" rel="noopener">Tap to open &amp; enter &rarr;</a>
<div class="meta">${x.tag=='Local'?'<span class="tag loc">Local</span>':''}<span class="tag">${x.est_value?'$'+Number(x.est_value).toLocaleString():'value ?'}</span><span class="tag">ends ${e(x.end_date||'?')}</span><span class="tag">${e(x.frequency)}</span>${x.won?'<span class="tag">WON: '+e(x.won)+'</span>':''}<br>${e(x.source)} · seen ${e(x.first_seen)}${x.entered_date?' · entered '+e(x.entered_date):''}</div></div>`).join('')||'<p>No items</p>'}
['s','q','h','t','x'].forEach(i=>document.getElementById(i).addEventListener('input',r));r();</script></body></html>"""
    with open(HTML_PATH, "w", encoding="utf-8") as f: f.write(page)

if __name__ == "__main__":
    main()
