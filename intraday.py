#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  DAILY INTRADAY PICKS — Auto Top-10 Scanner (NSE CASH, LONG + SHORT)
# ------------------------------------------------------------
#  Philosophy : 100% perfect strategy exist nahi karti. Asli edge =
#               chhota fixed risk + 1:2 RR + discipline (max 3 trades/day).
#  Data       : 100% NSE/BSE EOD bhavcopy — YAHOO POORA HATA DIYA.
#               ~40 sessions ki bhavcopy history se 20D high/low, 20SMA,
#               ATR(14), volume ratio, RS, delivery% sab banta hai.
#               FII/DII flows NSE se. Live/realtime data ki zaroorat NAHI —
#               plan close ke baad banta hai, execute open ke baad hota hai.
#  Direction  : LONG  (20D breakout family) + SHORT (20D breakdown family).
#               Cash intraday MIS short selling allowed hai — sell first,
#               same day buy-back, 3:20 tak square off.
#  Guarantee  : MIN 5 picks — score tiers relax hote hain (LOW CONF /
#               WEAK PASS tags dikhte hain). Force trade phir bhi mat karo.
#  Run local  : python intraday.py               (browser khud khulega)
#  Selftest   : python intraday.py --selftest    (zero network, synthetic)
#  GitHub     : roz 8:00 PM IST auto-run -> plan.html / picks.csv / history.csv
#  Yeh educational research tool hai — financial advice NAHI.
# ============================================================

import os
import csv
import io
import sys
import json
import time
import html
import random
import zipfile
import webbrowser
import datetime as dt
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("requests missing hai:  pip install requests")
    sys.exit(1)

# ---------------- CONFIG (env-overridable) ----------------
CAPITAL        = float(os.environ.get("CAPITAL", "150000"))
RISK           = float(os.environ.get("RISK", "400"))       # Rs per trade (fixed, non-negotiable)
RR             = float(os.environ.get("RR", "2"))           # 1:2 reward-risk
LEV            = float(os.environ.get("LEV", "5"))          # MIS 5x cap
TOP_N          = int(os.environ.get("TOP_N", "10"))
MIN_SC         = int(os.environ.get("MIN_SC", "7"))
MAX_PER_SECTOR = int(os.environ.get("MAX_PER_SECTOR", "3")) # diversification
SCAN_LIMIT     = int(os.environ.get("SCAN_LIMIT", "0"))     # 0 = full scan; testing ke liye
FORCE_TODAY    = os.environ.get("FORCE_TODAY", "").strip()  # DDMMYYYY — past-date paper run

HIST_DAYS      = int(os.environ.get("HIST_DAYS", "40"))     # kitne trading sessions ki history
MIN_PICKS      = 5                                          # guarantee — tier relaxation se bharti hai

SELFTEST = "--selftest" in sys.argv
OUTP     = "selftest_" if SELFTEST else ""                   # selftest outputs alag files me

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

NSE_HOSTS   = ["https://nsearchives.nseindia.com",
               "https://archives.nseindia.com",
               "https://www1.nseindia.com"]
NSE_UDIFF   = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
BSE_UDIFF   = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV"
FIIDII_PATH = "/content/equities/eq_fiidii_hist_csv.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})

MIN_PRICE = 50.0
MIN_TOV   = 500.0          # Rs 5cr = 500 lakh
PROXIES   = ("NIFTYBEES", "SETFNIF50", "HDFCNIFTY", "UTINEXTIQ")   # Nifty-50 ETF regime proxy
ETF_BLACKLIST = {"NIFTYBEES", "SETFNIF50", "HDFCNIFTY", "UTINEXTIQ",
                 "LIQUIDBEES", "GOLDBEES", "SILVERBEES", "BANKBEES", "ITBEES"}

# NSE/BSE block detect — ek baar 403 aaya to baaki dates pe mehnat waste nahi
DEAD = {"nse": False, "bse": False}

# ---------------- chhote helpers ----------------

def log(msg):
    print(msg, flush=True)

def today_ist():
    """FORCE_TODAY (DDMMYYYY) ho to wahi date (8 PM maan lo), warna real IST now."""
    if FORCE_TODAY:
        try:
            d = dt.datetime.strptime(FORCE_TODAY, "%d%m%Y")
            return d.replace(tzinfo=IST, hour=20, minute=0)
        except ValueError:
            log(f"  !! FORCE_TODAY format galat ({FORCE_TODAY}) — real time use kar raha hoon")
    return dt.datetime.now(IST)

def compute_plan_day():
    """9:15 AM se pehle run -> plan-day = AAJ. Baaki -> agla din. Weekend skip."""
    d = today_ist().date()
    if today_ist().time() >= dt.time(9, 15):
        d = d + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d = d + dt.timedelta(days=1)
    return d

def inr(x):
    """Indian grouping: 150000 -> 1,50,000"""
    neg = x < 0
    x = abs(int(round(x)))
    s = str(x)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s

def fnum(s):
    s = (s or "").strip().replace(",", "").replace('"', "")
    if s in ("", "-", "--", "NA", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

def http_get(url, referer=None, tries=1, timeout=25):
    """GET -> text ya None. Failure pe chup-chaap None (caller sambhalega)."""
    hdr = {"Referer": referer} if referer else {}
    for t in range(tries):
        try:
            r = SESSION.get(url, headers=hdr, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text
            if r.status_code == 403:
                return None                      # block — retry fayda nahi
            if r.status_code == 429:
                time.sleep(1.5 * (t + 1))
                continue
        except requests.RequestException:
            pass
        time.sleep(0.6 * (t + 1))
    return None

def http_get_bytes(url, referer=None, timeout=30):
    hdr = {"Referer": referer} if referer else {}
    try:
        r = SESSION.get(url, headers=hdr, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
    except requests.RequestException:
        pass
    return None

# ================= UNIVERSE =================
SECTOR_MAP = {
    "Automobile and Auto Components": "AUTO",
    "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "OILGAS",
    "Information Technology": "IT",
    "Financial Services": "FINANCE",
    "Healthcare": "PHARMA",
    "Capital Goods": "CAPGOODS",
    "Consumer Durables": "CONSDUR",
    "Construction Materials": "CEMENT",
    "Metals & Mining": "METAL",
    "Construction": "INFRA",
    "Chemicals": "CHEM",
    "Consumer Services": "CONSSVC",
    "Media Entertainment & Publication": "MEDIA",
    "Telecommunication": "TELECOM",
    "Realty": "REALTY",
    "Power": "POWER",
    "Services": "SERVICES",
    "Textiles": "TEXTILE",
    "Diversified": "DIV",
    "Hospitality": "HOSP",
    "Forest Materials": "OTHER",
}

def short_sector(name):
    name = (name or "").strip()
    if not name:
        return "OTHER"
    if name in SECTOR_MAP:
        return SECTOR_MAP[name]
    return name.split()[0].upper()[:10]

# Built-in fallback: sector-diverse liquid large/mid caps.
# NSE/BSE archives cloud IPs ko 403 maar dete hain — tab yehi list kaam aati hai.
_FALLBACK_RAW = """
BANK: HDFCBANK ICICIBANK SBIN KOTAKBANK AXISBANK INDUSINDBK PNB FEDERALBNK BANKBARODA CANBK UNIONBANK
FINANCE: BAJFINANCE BAJAJFINSV JIOFIN CHOLAFIN PFC RECLTD IRFC LTF SHRIRAMFIN MUTHOOTFIN
INSUR: SBILIFE HDFCLIFE ICICIPRULI ICICIGI
CAPMKT: BSE MCX CDSL CAMS ANGELONE
IT: TCS INFY HCLTECH WIPRO TECHM LTIM PERSISTENT COFORGE MPHASIS KPIT
PHARMA: SUNPHARMA DRREDDY CIPLA DIVISLAB AUROPHARMA ZYDUSLIFE TORNTPHARM LUPIN GLENMARK ALKEM
HOSP: APOLLOHOSP MAXHEALTH FORTIS LALPATHLAB METROPOLIS
AUTO: MARUTI M&M TATAMOTORS BAJAJ-AUTO EICHERMOT HEROMOTOCO TVSMOTOR ASHOKLEY ESCORTS
AUTOANC: MOTHERSON BOSCHLTD BALKRISIND APOLLOTYRE CEATLTD SONACOMS EXIDEIND UNOMINDA
FMCG: HINDUNILVR ITC NESTLEIND BRITANNIA MARICO TATACONSUM VBL DABUR GODREJCP COLPAL
OILGAS: RELIANCE ONGC IOC BPCL GAIL OIL PETRONET
POWER: NTPC POWERGRID TATAPOWER ADANIPOWER JSWENERGY CGPOWER SUZLON NHPC TORNTPOWER
METAL: TATASTEEL JSWSTEEL HINDALCO VEDL JINDALSTEL NMDC SAIL NATIONALUM APLAPOLLO
CEMENT: ULTRACEMCO GRASIM SHREECEM AMBUJACEM ACC DALBHARAT RAMCOCEM JKCEMENT
INFRA: LT RVNL CONCOR GMRAIRPORT
CAPGOODS: SIEMENS ABB BHEL CUMMINSIND POLYCAB HAVELLS VOLTAS BLUESTARCO CROMPTON DIXON KEI ASTRAL
DEFENCE: HAL BEL MAZDOCK COCHINSHIP GRSE TITAGARH
CONSDUR: TITAN KAJARIACER?
CHEM: PIDILITIND SRF AARTIIND DEEPAKNTR TATACHEM PIIND UPL ASIANPAINT
TELECOM: BHARTIARTL INDUSTOWER TATACOMM
REALTY: DLF GODREJPROP OBEROIRLTY LODHA PRESTIGE PHOENIXLTD
RETAIL: TRENT ABFRL PAGEIND JUBLFOOD
NEWAGE: ETERNAL PAYTM POLICYBZR IRCTC NAUKRI
MEDIA: ZEEL SUNTV PVRINOX
TRAVEL: INDIGO
LOGISTICS: DELHIVERY TATATECH
"""
FALLBACK_UNIVERSE = {}
for _line in _FALLBACK_RAW.strip().splitlines():
    _p = _line.split(":")
    for _s in _p[1].split():
        _s = _s.strip().upper()
        if _s.isalnum() or "-" in _s or "&" in _s:      # KAJARIACER? jaisi typos hatao
            FALLBACK_UNIVERSE[_s] = _p[0].strip()
FALLBACK_UNIVERSE.pop("KAJARIACER?", None)

def load_universe():
    """Nifty-500 official CSV -> {sym: sector}. Fail -> built-in fallback."""
    for host in NSE_HOSTS:
        txt = http_get(host + "/content/indices/ind_nifty500list.csv",
                       referer="https://www.nseindia.com/", tries=1)
        if not txt:
            continue
        try:
            rows = list(csv.DictReader(io.StringIO(txt), skipinitialspace=True))
        except Exception:
            continue
        uni = {}
        for r in rows:
            sym = (r.get("Symbol") or "").strip().upper()
            ser = (r.get("Series") or "").strip().upper()
            if not sym or (ser and ser != "EQ"):
                continue
            uni[sym] = short_sector(r.get("Industry"))
        if len(uni) >= 300:
            return uni, f"NIFTY500 ({len(uni)})"
    return dict(FALLBACK_UNIVERSE), f"FALLBACK ({len(FALLBACK_UNIVERSE)})"

# ================= BHAVCOPY PARSERS =================
def parse_sec_full(txt, d):
    """NSE sec_bhavdata_full CSV -> {sym: row}. Delivery % milta hai (best source)."""
    try:
        lines = txt.splitlines()
        if len(lines) < 2:
            return None
        hdr = [h.strip().upper() for h in lines[0].split(",")]
        idx = {h: i for i, h in enumerate(hdr)}

        def col(*names):
            for n in names:
                if n in idx:
                    return idx[n]
            return None

        c_sym, c_ser, c_date = col("SYMBOL"), col("SERIES"), col("DATE1", "DATE")
        c_o, c_h, c_l, c_c = col("OPEN_PRICE"), col("HIGH_PRICE"), col("LOW_PRICE"), col("CLOSE_PRICE")
        c_v, c_t, c_d = col("TTL_TRD_QNTY"), col("TURNOVER_LACS"), col("DELIV_PER")
        if None in (c_sym, c_ser, c_date, c_o, c_h, c_l, c_c, c_v):
            return None
        out = {}
        for ln in lines[1:]:
            p = ln.split(",")
            if len(p) < len(hdr) - 1:
                continue
            if p[c_ser].strip().upper() != "EQ":
                continue
            sym = p[c_sym].strip().upper()
            if not sym:
                continue
            o, h, l, c = fnum(p[c_o]), fnum(p[c_h]), fnum(p[c_l]), fnum(p[c_c])
            v = fnum(p[c_v])
            if c <= 0 or h <= 0 or l <= 0 or v <= 0:
                continue
            out[sym] = {"s": sym, "d": d, "o": o, "h": h, "l": l, "c": c, "v": v,
                        "tov": fnum(p[c_t]) if c_t is not None else 0.0,
                        "dp": fnum(p[c_d]) if c_d is not None else 0.0}
        return out if len(out) > 500 else None
    except Exception:
        return None

def parse_udiff(txt, d):
    """UDiFF common bhavcopy (NSE zip member ya BSE CSV) -> {sym: row}. Delivery NAHI milta."""
    try:
        rows = list(csv.DictReader(io.StringIO(txt), skipinitialspace=True))
    except Exception:
        return None
    out = {}
    for r in rows:
        sym = (r.get("TckrSymb") or "").strip().upper()
        if not sym:
            continue
        fit = (r.get("FinInstrmTp") or "").strip().upper()
        if "IDX" in fit:                       # index rows hatao
            continue
        o, h, l, c = (fnum(r.get("OpnPric")), fnum(r.get("HghPric")),
                      fnum(r.get("LwPric")), fnum(r.get("ClsPric")))
        v = fnum(r.get("TtlTradgVol"))
        if c <= 0 or h <= 0 or l <= 0 or v <= 0:
            continue
        out[sym] = {"s": sym, "d": d, "o": o, "h": h, "l": l, "c": c, "v": v,
                    "tov": fnum(r.get("TtlTrfVal")) / 1e5,   # Rs -> lakh
                    "dp": 0.0}
    return out if len(out) > 500 else None

def probe_nse():
    """Ek recent weekday ka bhavcopy try karke pata karo NSE zinda hai ya block.
    Block ho to 70 dates pe bekar 403-ratelag waste na ho — seedha BSE fallback."""
    d = today_ist().date()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    for host in NSE_HOSTS[:2]:
        txt = http_get(f"{host}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
                       referer="https://www.nseindia.com/", tries=1, timeout=20)
        if txt and parse_sec_full(txt, d):
            return True
    return False

def fetch_session(d):
    """Ek date ka bhavcopy — NSE zinda hai to NSE full (delivery ke saath),
    warna BSE UDiFF. -> ({sym: row}, source_str) ya (None, None)"""
    if not DEAD["nse"]:
        for host in NSE_HOSTS[:2]:
            txt = http_get(f"{host}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
                           referer="https://www.nseindia.com/", tries=1)
            if txt:
                rows = parse_sec_full(txt, d)
                if rows:
                    return rows, "NSE-FULL"
        # NSE UDiFF zip (delivery nahi milta, par OHLCV milta hai) — kabhi-kabhi
        # sec_bhavdata_full late hota hai par UDiFF aa chuka hota hai
        blob = http_get_bytes("https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_"
                              + d.strftime("%Y%m%d") + "_F_0000.csv.zip",
                              referer="https://www.nseindia.com/")
        if blob:
            try:
                zf = zipfile.ZipFile(io.BytesIO(blob))
                name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
                rows = parse_udiff(zf.read(name).decode("utf-8", "ignore"), d)
                if rows:
                    return rows, "NSE-UDIFF"
            except Exception:
                pass
        return None, None            # NSE zinda hai par is date ka file nahi (holiday) — BSE same holiday
    # NSE block hai -> BSE UDiFF (delivery % nahi milega — dp=0)
    blob = http_get_bytes(BSE_UDIFF.format(d=d.strftime("%Y%m%d")),
                          referer="https://www.bseindia.com/")
    if blob:
        try:
            txt = blob.decode("utf-8", "ignore")
            rows = parse_udiff(txt, d)
            if rows:
                return rows, "BSE-UDIFF"
        except Exception:
            pass
    return None, None

def build_history():
    """Last ~70 calendar days (weekdays) ke bhavcopy parallel download.
    -> (hist {sym: [rows sorted]}, sessions [dates desc], src Counter, data_date)"""
    d0 = today_ist().date()
    days = []
    d = d0
    while len(days) < 70:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)

    got = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_session, day): day for day in days}
        done = 0
        for f in as_completed(futs):
            day, (rows, src) = futs[f], f.result()
            done += 1
            if rows:
                got[day] = (rows, src)
            if done % 20 == 0:
                log(f"      bhavcopy sessions: {len(got)} mil gaye ({done}/{len(days)} try)")

    # host-block detect: pehle 5 sessions bhi nahi mile to us source ko dead maano
    # (agla run phir try karega)
    keep = sorted(got.keys(), reverse=True)[:HIST_DAYS]
    hist = {}
    src_ct = Counter()
    for day in keep:
        rows, src = got[day]
        src_ct[src] += 1
        for sym, row in rows.items():
            hist.setdefault(sym, []).append(row)
    for sym in hist:
        hist[sym].sort(key=lambda r: r["d"])
    return hist, keep, src_ct

# ================= FII / DII =================
def parse_fiidii_csv(txt):
    """NSE eq_fiidii_hist_csv.csv -> [{d, fii, dii}] (Rs Cr, date desc)."""
    try:
        lines = txt.splitlines()
        if len(lines) < 2:
            return []
        hdr = [h.strip().strip('"').upper() for h in lines[0].split(",")]
        i_f = i_d = None
        for i, h in enumerate(hdr):
            if "FII" in h and "NET" in h:
                i_f = i
            if "DII" in h and "NET" in h:
                i_d = i
        if i_f is None or i_d is None:
            return []
        out = []
        for ln in lines[1:]:
            p = [x.strip().strip('"') for x in ln.split(",")]
            if len(p) <= max(i_f, i_d):
                continue
            d = None
            for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    d = dt.datetime.strptime(p[0].strip(), fmt).date()
                    break
                except ValueError:
                    continue
            if not d:
                continue
            out.append({"d": d, "fii": fnum(p[i_f]), "dii": fnum(p[i_d])})
        out.sort(key=lambda r: r["d"], reverse=True)
        return out[:6]
    except Exception:
        return []

def fiidii_api():
    """NSE website API (cookie warmup ke saath) — hist CSV fail hone pe."""
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
        s.get("https://www.nseindia.com/reports/fii-dii", timeout=15)
        r = s.get("https://www.nseindia.com/api/fiiDiiDisplayGraphData", timeout=15,
                  headers={"Referer": "https://www.nseindia.com/reports/fii-dii",
                           "X-Requested-With": "XMLHttpRequest"})
        if r.status_code != 200:
            return []
        j = r.json()
        arr = j.get("fiidiiData") if isinstance(j, dict) else j
        if not arr:
            return []
        by_date = {}
        for e in arr:
            cat = (e.get("category") or "").upper()
            d = None
            for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%b-%y"):
                try:
                    d = dt.datetime.strptime((e.get("date") or "").strip(), fmt).date()
                    break
                except ValueError:
                    continue
            if not d:
                continue
            net = fnum(str(e.get("netValue", 0)))
            slot = by_date.setdefault(d, {"d": d, "fii": 0.0, "dii": 0.0})
            if cat.startswith("FII") or cat.startswith("FPI"):
                slot["fii"] += net
            elif cat.startswith("DII"):
                slot["dii"] += net
        return [{"d": k, "fii": v["fii"], "dii": v["dii"]}
                for k, v in sorted(by_date.items(), reverse=True)
                if v["fii"] or v["dii"]][:6]
    except Exception:
        return []

def fetch_fiidii():
    for host in NSE_HOSTS[:2]:
        txt = http_get(host + FIIDII_PATH, referer="https://www.nseindia.com/", tries=1)
        if txt:
            rows = parse_fiidii_csv(txt)
            if rows:
                return rows, "NSE hist CSV"
    rows = fiidii_api()
    if rows:
        return rows, "NSE API"
    return [], None

def fii_verdict(rows):
    """Latest day ke flows se sentiment line."""
    if not rows:
        return None
    t = rows[0]
    f, d, tot = t["fii"], t["dii"], t["fii"] + t["dii"]
    if f <= -2000 and d < 0:
        msg, tone = "FII THOK KE SELLING + DII bhi negative — LONG size AADHA, SHORT setups priority", "bad"
    elif f <= -2000:
        msg, tone = "FII heavy selling, DII absorb kar raha — mixed tape, tight SL", "warn"
    elif f >= 2000:
        msg, tone = "FII heavy buying — breakout LONGS ke liye tailwind", "good"
    elif tot > 0:
        msg, tone = "Flows neutral-positive — normal size chalega", "ok"
    else:
        msg, tone = "Flows mildly negative — tight SL, quick exits", "ok"
    return {"date": t["d"], "fii": f, "dii": d, "tot": tot, "msg": msg, "tone": tone}

# ================= INDICATORS =================
def atr14(rows, i, n=14):
    """Wilder ATR."""
    if i < n:
        return None
    trs = []
    for j in range(1, i + 1):
        h, l, pc = rows[j]["h"], rows[j]["l"], rows[j - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a

# ================= EVALUATE (LONG + SHORT) =================
def evaluate(sym, sector, rows, nret20):
    """Ek stock, dono direction score. Best direction return (ya reject reason).
    Rejects har direction ke liye alag — stock 6% upar ja chuka ho to LONG reject,
    par wahi stock 20D low tod raha ho to SHORT valid ho sakta hai."""
    i = len(rows) - 1
    if i < 21:
        return None, "DATA<22d"
    t = rows[i]
    close = t["c"]

    if sym in ETF_BLACKLIST:
        return None, "ETF"
    if close < MIN_PRICE:
        return None, "PENNY(<50)"
    if t["tov"] < MIN_TOV:
        return None, "ILLIQUID(<5cr)"
    if t["h"] <= t["l"] or t["c"] <= 0:
        return None, "BAD-ROW"

    prior = rows[max(0, i - 20):i]
    if len(prior) < 20:
        return None, "DATA<22d"
    hi20 = max(r["h"] for r in prior)
    lo20 = min(r["l"] for r in prior)
    avgv = sum(r["v"] for r in prior) / 20.0
    vol_x = t["v"] / avgv if avgv > 0 else 0.0

    atr = atr14(rows, i)
    if not atr or atr <= 0:
        return None, "ATR-ERR"
    atr_pct = atr / close
    if atr_pct < 0.006:
        return None, "DEAD(ATR<0.6%)"

    rng = t["h"] - t["l"]
    cpos = (t["c"] - t["l"]) / rng if rng > 0 else 0.5

    closes = [r["c"] for r in rows]
    sma20 = sum(closes[i - 19:i + 1]) / 20.0
    sma20_old = sum(closes[i - 24:i - 4]) / 20.0 if i >= 24 else None
    ret20 = closes[i] / closes[i - 20] - 1.0 if closes[i - 20] > 0 else None
    rs = (ret20 - nret20) if (ret20 is not None and nret20 is not None) else None
    dp = t["dp"]

    def score_dir(direction):
        """-> (score, tags, entry, sl, rps, qty) ya None (direction-specific reject / low score)"""
        tags = []
        if direction == "LONG":
            if close >= hi20 * 1.05:
                return None
            entry = t["h"] * 1.001
            sc = 0
            if close > hi20:
                sc += 3; tags.append("20D BREAKOUT")
            elif close >= hi20 * 0.995:
                sc += 2; tags.append("HIGH KE PAAS")
            if vol_x >= 1.8:
                sc += 2; tags.append(f"VOL {vol_x:.1f}x")
            elif vol_x >= 1.3:
                sc += 1; tags.append(f"VOL {vol_x:.1f}x")
            if close > t["o"]:
                sc += 1; tags.append("GREEN")
            if cpos >= 0.70:
                sc += 1; tags.append("STRONG CLOSE")
            if close > sma20:
                if sma20_old is not None and sma20 > sma20_old:
                    sc += 2; tags.append("UPTREND+")
                else:
                    sc += 1; tags.append("UPTREND")
            if rs is not None and rs >= 0.03:
                sc += 1; tags.append("RS>NIFTY")
            if 0.01 <= atr_pct <= 0.05:
                sc += 1; tags.append("ATR SWEET")
            if dp >= 40:
                sc += 1; tags.append(f"DELIV {dp:.0f}%")
            sl = entry - 0.7 * atr
        else:
            if close <= lo20 * 0.95:
                return None
            entry = t["l"] * 0.999
            sc = 0
            if close < lo20:
                sc += 3; tags.append("20D BREAKDOWN")
            elif close <= lo20 * 1.005:
                sc += 2; tags.append("LOW KE PAAS")
            if vol_x >= 1.8:
                sc += 2; tags.append(f"VOL {vol_x:.1f}x")
            elif vol_x >= 1.3:
                sc += 1; tags.append(f"VOL {vol_x:.1f}x")
            if close < t["o"]:
                sc += 1; tags.append("RED")
            if cpos <= 0.30:
                sc += 1; tags.append("WEAK CLOSE")
            if close < sma20:
                if sma20_old is not None and sma20 < sma20_old:
                    sc += 2; tags.append("DOWNTREND+")
                else:
                    sc += 1; tags.append("DOWNTREND")
            if rs is not None and rs <= -0.03:
                sc += 1; tags.append("RS<NIFTY")
            if 0.01 <= atr_pct <= 0.05:
                sc += 1; tags.append("ATR SWEET")
            if dp >= 40:
                sc += 1; tags.append(f"DELIV {dp:.0f}%")
            sl = entry + 0.7 * atr

        rps = abs(sl - entry)                          # risk per share
        if rps / entry < 0.004:
            return None
        qty = min(int(RISK // rps), int((CAPITAL * LEV) // entry))
        if qty < 1:
            return None
        return sc, tags, entry, sl, rps, qty

    best = None
    for direction in ("LONG", "SHORT"):
        res = score_dir(direction)
        if res is None:
            continue
        sc, tags, entry, sl, rps, qty = res
        if sc < 4:                                     # pool floor
            continue
        cand = {"symbol": sym, "sector": sector, "direction": direction, "score": sc,
                "tags": tags,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tgt": round(entry + RR * rps, 2) if direction == "LONG"
                       else round(entry - RR * rps, 2),
                "qty": qty,
                "risk": round(qty * rps, 2),
                "gain": round(qty * RR * rps, 2),
                "atr_pct": atr_pct * 100, "vol_x": vol_x, "deliv": dp,
                "close": close, "volume": t["v"],
                "signal_date": t["d"],
                "turnover": t["tov"]}
        if best is None or sc > best["score"]:
            best = cand
    if best is None:
        return None, None                              # na reject, na candidate = low score
    return best, None

# ================= PICK FINALIZATION (MIN-5 GUARANTEE) =================
def final_picks(pool, weak=False):
    """Score+volume sort, sector cap, TOP_N. Garanty: kam se kam MIN_PICKS —
    tier relax (MIN_SC -> -1 -> -2 -> best-remaining) LOW CONF / WEAK PASS tag ke saath.
    WEAK market me SHORT setups ko priority (aadhe slot) — girta bazaar me
    breakdown shorts hi paisa banate hain."""
    pool.sort(key=lambda m: (-m["score"], -m["vol_x"], -m["turnover"]))
    picked, sec_cnt = [], Counter()

    def _take(m, tag=None):
        if sec_cnt[m["sector"]] >= MAX_PER_SECTOR:
            return False
        if tag:
            m["tags"].append(tag)
        picked.append(m)
        sec_cnt[m["sector"]] += 1
        return True

    def fill(minsc, limit, tag=None, direction=None):
        cur = sum(1 for m in picked if direction is None or m["direction"] == direction)
        for m in pool:
            if cur >= limit:
                break
            if direction is None and len(picked) >= TOP_N:
                break
            if m in picked or m["score"] < minsc:
                continue
            if direction and m["direction"] != direction:
                continue
            if _take(m, tag):
                cur += 1

    if weak and any(m["direction"] == "SHORT" for m in pool):
        # WEAK market: pehle aadhe slot SHORT setups ke (tag ke saath), baaki generic fill
        fill(MIN_SC - 2, TOP_N // 2, "WEAK-MKT BIAS", direction="SHORT")
    fill(MIN_SC, TOP_N)                                # core: proper signals
    if len(picked) < MIN_PICKS:
        fill(MIN_SC - 1, MIN_PICKS, "LOW CONF")
    if len(picked) < MIN_PICKS:
        fill(MIN_SC - 2, MIN_PICKS, "LOW CONF")
    if len(picked) < MIN_PICKS:
        fill(0, MIN_PICKS, "WEAK PASS")
    if len(picked) < TOP_N:                            # aage badhao (>=5 score, untagged)
        fill(MIN_SC - 2, TOP_N)
    return picked

# ================= MARKET REGIME (Nifty ETF proxy) =================
def market_state(hist):
    """NIFTYBEES (ya koi Nifty ETF) bhavcopy history se — Yahoo ki jagah."""
    for px in PROXIES:
        rows = hist.get(px) or []
        if len(rows) >= 22:
            closes = [r["c"] for r in rows]
            i = len(closes) - 1
            sma20 = sum(closes[i - 19:i + 1]) / 20.0
            sma_old = sum(closes[i - 24:i - 4]) / 20.0 if i >= 24 else None
            ret20 = closes[i] / closes[i - 20] - 1.0 if closes[i - 20] > 0 else None
            falling = sma_old is not None and sma20 < sma_old
            rising = sma_old is not None and sma20 > sma_old
            c = closes[i]
            if c < sma20 and falling:
                state = "WEAK"
            elif c < sma20:
                state = "BELOW-SMA"
            elif c > sma20 and rising:
                state = "STRONG"
            else:
                state = "NEUTRAL"
            return {"proxy": px, "state": state, "close": c, "sma20": sma20,
                    "ret20": ret20, "date": rows[-1]["d"]}
    return None

# ================= OUTPUT: CONSOLE =================
def print_console_table(picks, plan_day, mkt, fv):
    W = 118
    print()
    print("=" * W)
    print(f" AAJ KE {len(picks)} PICKS — {plan_day.strftime('%a, %d %b %Y')} ke liye"
          f"  |  LONG = breakout buy  |  SHORT = breakdown sell (MIS)")
    print("=" * W)
    if mkt:
        print(f" MARKET: {mkt['state']}  ({mkt['proxy']} {mkt['close']:,.2f} vs 20SMA {mkt['sma20']:,.2f})")
    if fv:
        print(f" FII {fv['fii']:+,.0f} Cr | DII {fv['dii']:+,.0f} Cr ({fv['date']:%d %b}) — {fv['msg']}")
    print("-" * W)
    if not picks:
        print(" KOI PICK NAHI — no-trade day bhi ek position hai. Cash sitting = capital safe.")
    else:
        print(f"{'#':<3}{'SYMBOL':<11}{'DIR':<6}{'SEC':<9}{'SC':>3}{'ENTRY':>10}{'SL':>10}"
              f"{'TARGET':>10}{'QTY':>7}{'RISK Rs':>10}{'GAIN Rs':>10}  REASON")
        print("-" * W)
        for i, m in enumerate(picks, 1):
            print(f"{i:<3}{m['symbol']:<11}{m['direction']:<6}{m['sector']:<9}{m['score']:>3}"
                  f"{m['entry']:>10.2f}{m['sl']:>10.2f}{m['tgt']:>10.2f}{m['qty']:>7}"
                  f"{m['risk']:>10.0f}{m['gain']:>10.0f}  {' | '.join(m['tags'])}")
    print("=" * W)

# ================= OUTPUT: CSV =================
HEADER = ["PLAN_DATE", "SIGNAL_DATE", "SYMBOL", "DIRECTION", "SECTOR", "SCORE", "ENTRY", "SL",
          "TARGET", "QTY", "RISK_RS", "GAIN_RS", "ATR_PCT", "VOL_X", "DELIV_PCT", "CLOSE",
          "REGIME", "REASON", "RUN_TS"]

def csv_rows(picks, plan_day, mkt, run_ts):
    reg = mkt["state"] if mkt else "NA"
    out = []
    for m in picks:
        out.append([plan_day.isoformat(), m["signal_date"].isoformat(), m["symbol"],
                    m["direction"], m["sector"], m["score"], m["entry"], m["sl"], m["tgt"],
                    m["qty"], m["risk"], m["gain"], round(m["atr_pct"], 2), round(m["vol_x"], 2),
                    round(m["deliv"], 1), m["close"], reg, " | ".join(m["tags"]), run_ts])
    return out

def write_picks_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

def write_history_csv(path, rows, plan_day):
    """Append-only audit log. Same plan-date ke purane rows replace (re-run safe)."""
    old = []
    if os.path.exists(path):
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                rd = csv.reader(f)
                next(rd, None)
                old = [r for r in rd if r and r[0] != plan_day.isoformat()]
        except Exception:
            old = []
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(old)
        w.writerows(rows)

# ================= OUTPUT: HTML =================
CSS = """
:root{--amber:#ffb300;--bg:#0d0c08;--panel:#14110a;--line:#2a2517;--txt:#e8e0c8;
      --dim:#9a927a;--green:#7ddb7d;--red:#ff7b72}
*{box-sizing:border-box}
body{margin:0;padding:22px 14px;background:var(--bg);color:var(--txt);
     font-family:Consolas,'Courier New',monospace;font-size:14px}
.wrap{max-width:1280px;margin:0 auto}
h1{color:var(--amber);font-size:21px;margin:0 0 6px;letter-spacing:.5px}
.meta{color:var(--dim);font-size:12.5px;line-height:1.8;margin-bottom:12px}
.patch{color:var(--green)}
.warn{color:#ff9d66}
.banner{border:1px solid var(--red);background:#2a0f0f;color:#ffb4ae;padding:10px 14px;
        margin:10px 0;font-weight:bold;font-size:13.5px;line-height:1.6}
.fii{border:1px solid var(--line);background:var(--panel);padding:12px 16px;margin:10px 0}
.fii h2{color:var(--amber);font-size:14px;margin:0 0 8px}
.fii table{font-size:12.5px;min-width:0;width:auto}
.fii th,.fii td{padding:4px 14px 4px 0;border-bottom:1px solid #1e1a10}
.fii th{color:var(--dim);border-bottom:1px solid var(--line);font-size:11.5px}
.pos{color:var(--green);font-weight:bold}
.neg{color:var(--red);font-weight:bold}
.verdict{margin-top:8px;font-size:12.5px}
.verdict.bad{color:#ffb4ae}.verdict.warn{color:#ff9d66}
.verdict.good{color:var(--green)}.verdict.ok{color:var(--dim)}
.tablewrap{overflow-x:auto;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:1080px}
th{background:#171309;color:var(--amber);text-align:right;padding:9px 8px;
   border-bottom:2px solid var(--amber);white-space:nowrap;font-size:12px}
th.l,td.l{text-align:left}
td{padding:8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
tr:hover td{background:#1a160c}
tr.rL td{background:rgba(125,219,125,.045)}
tr.rS td{background:rgba(255,123,114,.06)}
tr.rS:hover td{background:rgba(255,123,114,.1)}
.dirL{color:var(--green);font-weight:bold}
.dirS{color:var(--red);font-weight:bold}
.sym{color:var(--amber);font-weight:bold}
.sec{color:#c8b78a;font-size:12px}
.sc{color:var(--amber);font-weight:bold}
.sc9{color:#ffd54d;background:#2a230d;display:inline-block;padding:1px 8px;
     border:1px solid #6b5a1a;font-weight:bold}
.sl{color:var(--red)}
.tg{color:var(--green)}
.gn{color:var(--green);font-weight:bold}
.rsn{color:var(--dim);font-size:11.5px;text-align:left;white-space:normal;min-width:240px;line-height:1.55}
.legend{color:var(--dim);font-size:11.5px;margin:8px 0 2px;line-height:1.7}
.legend b.dl{color:var(--green)} .legend b.ds{color:var(--red)}
.rules{border:1px solid var(--line);background:var(--panel);padding:14px 18px;margin-top:18px}
.rules h2{color:var(--amber);font-size:15px;margin:0 0 8px}
.rules ol{margin:0;padding-left:22px;line-height:2;font-size:13px}
.empty{border:1px dashed #6b5a1a;color:var(--amber);padding:28px 20px;text-align:center;
       font-size:14px;margin-top:14px;line-height:1.9}
.foot{color:#6d664f;font-size:11px;margin-top:16px;line-height:1.8}
"""

def write_html(path, picks, plan_day, sig_date, uni_src, sessions_ct, n_sessions,
               mkt, fii_rows, fv, scanned, run_ts, secs):
    day_s = plan_day.strftime("%a, %d %b %Y")
    sig_s = sig_date.strftime("%a, %d %b %Y") if sig_date else "NA"

    mkt_s = ""
    if mkt:
        pos = "UPAR" if mkt["close"] >= mkt["sma20"] else "NEECHE"
        mkt_s = f" | MARKET: {mkt['state']} ({mkt['proxy']} 20SMA ke {pos})"

    if n_sessions > 0:
        src_s = " + ".join(f"{k} {v}" for k, v in sessions_ct.most_common())
        src_line = (f'<span class="patch">| Source: 100% NSE/BSE bhavcopy — Yahoo REMOVED ✓ '
                    f'({src_s}, {n_sessions} sessions)</span>')
    else:
        src_line = '<span class="warn">| Bhavcopy data nahi mila</span>'

    fii_meta = (f" | FII/DII: {'mil gaya' if fii_rows else 'unavailable'}") if not fii_rows else ""
    meta = (f"Signal: {sig_s} (close) | Universe: {scanned} liquid stocks ({uni_src}) | "
            f"Capital: ₹{inr(CAPITAL)} | Risk: ₹{inr(RISK)}/trade | RR: 1:{RR:g} | "
            f"Lev: {LEV:g}x MIS{mkt_s}{src_line}{fii_meta}<br>"
            f"<span style='color:#8f886f'>MIN-5 GUARANTEE: score tiers relax hote hain — "
            f"LOW CONF / WEAK PASS tag = kam confidence, aadha size ya skip better</span>")

    banner = ""
    if mkt and mkt["state"] in ("WEAK", "BELOW-SMA"):
        banner = ('<div class="banner">MARKET WEAK — Nifty 20SMA ke NEECHE. '
                  'LONG entries me size AADHA karo, SHORT setups ko priority do. '
                  'Gap-down short chase mat karo, gap-up long mat karo.</div>')

    # ---- FII/DII panel ----
    if fii_rows:
        frows = []
        for r in fii_rows:
            fc = "pos" if r["fii"] > 0 else "neg"
            dc = "pos" if r["dii"] > 0 else "neg"
            tot = r["fii"] + r["dii"]
            tc = "pos" if tot > 0 else "neg"
            frows.append(f"<tr><td>{r['d'].strftime('%d %b %a')}</td>"
                         f"<td class='{fc}'>{r['fii']:+,.0f}</td>"
                         f"<td class='{dc}'>{r['dii']:+,.0f}</td>"
                         f"<td class='{tc}'>{tot:+,.0f}</td></tr>")
        vtone = fv["tone"] if fv else "ok"
        vmsg = fv["msg"] if fv else ""
        fii_panel = ('<div class="fii"><h2>FII / DII FLOWS — CASH MARKET (₹ Crore, NSE)</h2>'
                     '<table><tr><th>DATE</th><th>FII NET</th><th>DII NET</th><th>TOTAL</th></tr>'
                     + "".join(frows) + "</table>"
                     + (f"<div class='verdict {vtone}'>&#9654; {vmsg}</div>" if vmsg else "")
                     + "</div>")
    else:
        fii_panel = ('<div class="fii"><h2>FII / DII FLOWS</h2>'
                     '<div class="verdict ok">Aaj NSE se FII/DII data nahi mila — '
                     'kal wapas try hoga. Flows sentiment ke liye hain, entry signal ke liye NAHI.</div></div>')

    # ---- picks table ----
    if picks:
        rows = []
        for i, m in enumerate(picks, 1):
            cls = "rL" if m["direction"] == "LONG" else "rS"
            dir_cls = "dirL" if m["direction"] == "LONG" else "dirS"
            sc_cls = "sc9" if m["score"] >= 9 else "sc"
            rows.append(
                f"<tr class='{cls}'><td>{i}</td>"
                f'<td class="l sym">{html.escape(m["symbol"])}</td>'
                f'<td class="l {dir_cls}">{m["direction"]}</td>'
                f'<td class="l sec">{html.escape(m["sector"])}</td>'
                f'<td><span class="{sc_cls}">{m["score"]}</span></td>'
                f'<td>{m["entry"]:.2f}</td>'
                f'<td class="sl">{m["sl"]:.2f}</td>'
                f'<td class="tg">{m["tgt"]:.2f}</td>'
                f'<td>{m["qty"]}</td>'
                f'<td>₹{inr(m["risk"])}</td>'
                f'<td class="gn">₹{inr(m["gain"])}</td>'
                f'<td>{m["atr_pct"]:.1f}%</td>'
                f'<td>{m["vol_x"]:.1f}x</td>'
                f'<td class="rsn">{html.escape(" · ".join(m["tags"]))}</td></tr>')
        table = ('<div class="tablewrap"><table>'
                 '<tr><th>#</th><th class="l">SYMBOL</th><th class="l">DIR</th><th class="l">SECTOR</th>'
                 '<th>SCORE</th><th>ENTRY</th><th>SL</th><th>TARGET</th><th>QTY</th><th>RISK ₹</th>'
                 '<th>GAIN @TGT</th><th>ATR%</th><th>VOL×</th><th class="l">REASON (score breakdown)</th></tr>'
                 + "".join(rows) + "</table></div>")
    else:
        table = ('<div class="empty">AAJ KOI PICK NAHI.<br>'
                 'No-trade day bhi ek position hai — cash sitting = capital safe.<br>'
                 'Force trade = account killer. Kal phir check karo.</div>')

    page = f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Intraday Picks — NSE Cash (LONG + SHORT)</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
<h1>AAJ KE {len(picks)} PICKS — {day_s} ke liye</h1>
<div class="meta">{meta}</div>
{banner}
<div class="legend"><b class="dl">LONG</b> = price ENTRY ke <b>UPAR</b> 5-min close ho to BUY &nbsp;|&nbsp;
<b class="ds">SHORT</b> = price ENTRY ke <b>NEECHE</b> 5-min close ho to SELL (MIS, 3:20 tak square-off)</div>
{table}
{fii_panel}
<div class="rules"><h2>KAL KE 5 RULES ({day_s} ke liye)</h2><ol>
<li>Entry sirf TRIGGER ke baad: LONG = ENTRY ke <b>UPAR</b> 5-min candle close; SHORT = ENTRY ke
<b>NEECHE</b> 5-min candle close. Bina trigger trade NAHI.</li>
<li><b>0.7%+ gap</b> ho to trade <b>SKIP</b> — gap-up pe long chase nahi, gap-down pe short chase nahi.</li>
<li>Entry ke saath hi <b>SL order</b> — SHORT ka SL entry ke UPAR hota hai. SL hit = turant bahar, no average down.</li>
<li><b>Max 3 trades/day</b>; 2 winner = ₹1,000 target poora = <b>BAND</b>. 3:00 PM ke baad naya entry nahi.</li>
<li>Result/RBI/expiry day pe <b>size aadha</b>. FII/DII flows sentiment hai — entry signal NAHI.</li>
</ol></div>
<div class="foot">Data: 100% NSE/BSE bhavcopy EOD (~{HIST_DAYS} sessions) + NSE FII/DII | Yahoo: NAHI hai | Auto-run: GitHub Actions, 8:00 PM IST (Mon-Fri)<br>
Educational research note — financial advice NAHI. Cash/MIS only, F&amp;O nahi. Bina SL trade = gambling.<br>
Generated: {run_ts} | Runtime: {secs:.0f}s</div>
</div></body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)

# ================= SELFTEST (synthetic data — network ZERO) =================
HISTS = {}

def _synth_days(n=60):
    d = today_ist().date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))

def _row(d, o, hi, lo, c, v, dp=0.0):
    return {"s": "", "d": d, "o": round(o, 2), "h": round(hi, 2), "l": round(lo, 2),
            "c": round(c, 2), "v": v, "tov": c * v / 1e5, "dp": dp}

def build_synth():
    """70 fake stocks. Styles: 0,5,6,7,8,9 = breakout LONG | 4 = breakdown SHORT |
    1 = penny | 2 = dead | 3 = overext. + NIFTYBEES proxy + fake FII/DII."""
    days = _synth_days()
    secs = ["IT", "BANK", "PHARMA", "AUTO", "FMCG", "METAL", "POWER", "FINANCE", "CHEM", "CONSDUR"]
    uni, hist_map = {}, {}
    for k in range(70):
        sym = f"T{k:02d}"
        uni[sym] = secs[k % len(secs)]
        rng = random.Random(sym)
        style = k % 10
        price = 90 + rng.random() * 800
        if style == 1:
            price = 32 + rng.random() * 10              # penny
        if style == 4:
            price = 150 + rng.random() * 400            # short candidate, penny na ho
        candles = []
        for d in days[:-1]:
            if style == 2:
                vol, drift = 0.004, 0.0002              # dead
            elif style == 4:
                vol, drift = 0.006, -0.002              # mild downtrend
            elif style in (7, 8, 9):
                vol, drift = 0.016, 0.0010              # strong uptrend
            else:
                vol, drift = 0.012, 0.0004
            o = price
            c = max(5.0, o * (1 + rng.gauss(drift, vol)))
            hi = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)) + 0.001)
            lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)) - 0.001)
            v = int(60000 + rng.random() * 250000)
            r = _row(d, o, hi, lo, c, v, dp=25 + rng.random() * 50)
            r["s"] = sym
            candles.append(r)
            price = c
        prev = candles[-1]
        avg_v = sum(x["v"] for x in candles[-20:]) / 20.0
        if style == 3:                                   # over-extended (+8%)
            o = prev["c"] * 1.001; c = prev["c"] * 1.08
            hi = c * 1.002; lo = o * 0.999; v = int(avg_v * 2.0)
        elif style == 4:                                 # BREAKDOWN day — short trigger
            o = prev["c"] * 0.998; c = prev["c"] * 0.975
            hi = o * 1.003; lo = c * 0.997; v = int(avg_v * 1.9)
        elif style in (0, 5, 6, 7, 8, 9):                # breakout day
            o = prev["c"] * 1.001; c = prev["c"] * (1.020 + rng.random() * 0.020)
            hi = c * 1.003; lo = o * 0.998; v = int(avg_v * (1.4 + rng.random() * 1.2))
        elif style == 1:                                 # penny dull red
            o = prev["c"] * 0.998; c = prev["c"] * 0.994
            hi = o * 1.002; lo = c * 0.997; v = int(avg_v * 0.9)
        else:                                            # flat
            o = prev["c"] * 1.0005; c = prev["c"] * (1 + rng.gauss(0, 0.004))
            hi = max(o, c) * 1.002; lo = min(o, c) * 0.998; v = int(avg_v * 1.1)
        r = _row(days[-1], o, hi, lo, c, v, dp=30 + rng.random() * 45)
        r["s"] = sym
        candles.append(r)
        hist_map[sym] = candles

    # Nifty ETF proxy (regime + RS)
    weak = bool(os.environ.get("SELFTEST_WEAK"))
    nf, p = [], 24000.0
    for j, d in enumerate(days):
        dr = -0.004 if (weak and j >= len(days) - 25) else 0.0004
        p *= (1 + random.Random(d.toordinal() * 7).gauss(dr, 0.005))
        r = _row(d, p, p, p, p, 1000000)
        r["s"] = "NIFTYBEES"
        nf.append(r)
    hist_map["NIFTYBEES"] = nf
    uni["NIFTYBEES"] = "PROXY"

    # fake FII/DII (₹ Cr)
    fii = []
    for j in range(5):
        d = days[-1 - j]
        if weak:
            fii.append({"d": d, "fii": float(-3200 - 400 * j), "dii": float(600 - 150 * j)})
        else:
            fii.append({"d": d, "fii": float(1500 + 250 * j), "dii": float(400 + 50 * j)})
    return uni, hist_map, days[-1], fii

# ================= MAIN =================
def main():
    t0 = time.time()
    run_ts = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    plan_day = compute_plan_day()
    mode = "SELFTEST (synthetic data)" if SELFTEST else "LIVE"
    log("=" * 100)
    log(f" DAILY INTRADAY PICKS — NSE CASH | LONG + SHORT | {mode}")
    log(f" Run: {run_ts}")
    log(f" CAPITAL=Rs {inr(CAPITAL)} | RISK=Rs {RISK:.0f} | RR=1:{RR:g} | LEV={LEV:g}x | "
        f"TOP_N={TOP_N} | MIN_SC={MIN_SC} | MIN_PICKS={MIN_PICKS} | MAX/SECTOR={MAX_PER_SECTOR}")
    log(f" PLAN DAY: {plan_day.strftime('%a, %d %b %Y')}"
        + (f"  (FORCE_TODAY={FORCE_TODAY})" if FORCE_TODAY else ""))
    log("=" * 100)

    # ---- [1] universe ----
    if SELFTEST:
        uni, HIST, sig_d, fii_rows = build_synth()
        uni_src = f"SYNTH ({len(uni)})"
        sessions = [r["d"] for r in HIST["NIFTYBEES"]]
        src_ct = Counter({"SYNTH": 1})
        fii_src = "SYNTH"
        log(f"[1/4] Universe: {uni_src} | history: synthetic {len(sessions)} sessions")
    else:
        uni, uni_src = load_universe()
        log(f"[1/4] Universe: {uni_src}")
        # ---- [2] bhavcopy history (40 sessions, NSE -> BSE fallback) ----
        DEAD["nse"] = not probe_nse()
        if DEAD["nse"]:
            log("      NSE archives block/down -> BSE UDiFF bhavcopy se history banegi (delivery % nahi milega)")
        log(f"[2/4] Bhavcopy history download ({HIST_DAYS} sessions, "
            f"{'BSE fallback' if DEAD['nse'] else 'NSE primary'})")
        HIST, sessions, src_ct = build_history()
        fii_rows, fii_src = fetch_fiidii()
        sig_d = sessions[0] if sessions else None

    if not SELFTEST and not sessions:
        log("\n!! KOI BHI bhavcopy session nahi mila — NSE+BSE dono blocked/down.")
        log("!! Outputs NAHI likhe (purane plan.html safe hai). Kuch der baad retry karo.")
        sys.exit(2)

    n_sess = len(sessions)
    if not SELFTEST:
        log(f"      Sessions loaded: {n_sess} (last: {sig_d}) | "
            + ", ".join(f"{k} {v}" for k, v in src_ct.most_common()))
        stale = (today_ist().date() - sig_d).days if sig_d else 99
        if stale > 4:
            log(f"      !! Data {stale} din purana lag raha hai — holiday stretch ya partial block")

    # ---- [3] market state + FII/DII ----
    mkt = market_state(HIST)
    nret20 = mkt["ret20"] if mkt else None
    if mkt:
        log(f"[3/4] MARKET: {mkt['state']} ({mkt['proxy']} {mkt['close']:,.2f} vs 20SMA "
            f"{mkt['sma20']:,.2f}, 20d ret {mkt['ret20']*100:+.1f}%)")
    else:
        log("[3/4] MARKET: NA — Nifty ETF proxy nahi mila (RS point skip hoga)")
    fv = fii_verdict(fii_rows)
    if fv:
        log(f"      FII {fv['fii']:+,.0f} Cr | DII {fv['dii']:+,.0f} Cr "
            f"({fv['date']:%d %b}, via {fii_src}) — {fv['msg']}")
    else:
        log("      FII/DII data nahi mila — sentiment note skip")

    # ---- [4] scan ----
    scan_syms = sorted(s for s in uni if s in HIST)
    if SCAN_LIMIT > 0:
        scan_syms = scan_syms[:SCAN_LIMIT]
    log(f"[4/4] Scan: {len(scan_syms)} stocks — LONG (breakout) + SHORT (breakdown) dono...")

    pool, skips = [], Counter()
    for n_, sym in enumerate(scan_syms, 1):
        cand, rej = evaluate(sym, uni.get(sym, "OTHER"), HIST[sym], nret20)
        if cand:
            pool.append(cand)
        elif rej:
            skips[rej] += 1
        if n_ % 150 == 0:
            log(f"      ... {n_}/{len(scan_syms)}")

    n_long_pool = sum(1 for m in pool if m["direction"] == "LONG")
    n_short_pool = len(pool) - n_long_pool
    log(f"      Pool: {len(pool)} candidates (LONG {n_long_pool} | SHORT {n_short_pool})")
    if skips:
        log("      Rejects: " + ", ".join(f"{k} x{v}" for k, v in skips.most_common(10)))

    picks = final_picks(pool, weak=bool(mkt and mkt["state"] in ("WEAK", "BELOW-SMA")))
    n_long = sum(1 for m in picks if m["direction"] == "LONG")
    n_short = len(picks) - n_long
    log(f"      PICKS: {len(picks)} (LONG {n_long} | SHORT {n_short})")

    # ---- outputs ----
    secs = time.time() - t0
    plan_html, picks_csv, hist_csv = f"{OUTP}plan.html", f"{OUTP}picks.csv", f"{OUTP}history.csv"

    rows = csv_rows(picks, plan_day, mkt, run_ts)
    write_picks_csv(picks_csv, rows)
    write_history_csv(hist_csv, rows, plan_day)
    write_html(plan_html, picks, plan_day, sig_d, uni_src if not SELFTEST else uni_src,
               src_ct, n_sess, mkt, fii_rows, fv, len(scan_syms), run_ts, secs)

    print_console_table(picks, plan_day, mkt, fv)
    log(f"      Files: {plan_html} | {picks_csv} | {hist_csv}  ({secs:.0f}s)")

    # selftest checks
    if SELFTEST:
        checks = [
            (len(picks) >= MIN_PICKS, f"MIN-5 guarantee (picks={len(picks)})"),
            (n_long >= 3, f"LONG picks {n_long} >= 3"),
            (n_short >= 2, f"SHORT picks {n_short} >= 2"),
            (all(m["score"] >= 4 for m in picks), "sab picks score >= 4"),
            (all(m["risk"] <= RISK + 0.01 for m in picks), f"risk <= Rs{RISK:.0f} per trade"),
            (max((Counter(m['sector'] for m in picks).values() or [0])) <= MAX_PER_SECTOR,
             f"sector cap {MAX_PER_SECTOR} respected"),
            (fv is not None, "FII/DII block parsed"),
        ]
        all_ok = True
        for ok, label in checks:
            log(f"      SELFTEST [{'PASS' if ok else 'FAIL'}] {label}")
            all_ok = all_ok and ok
        if not all_ok:
            sys.exit(3)
        log("      SELFTEST files (selftest_*) likhi gayi — real outputs untouched hain.")
        return

    # GitHub summary + browser
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        try:
            lines = [f"## Picks — {plan_day.strftime('%d %b %Y')} ke liye", "",
                     "| SYM | DIR | SC | ENTRY | SL | TGT | QTY |",
                     "|---|---|---|---|---|---|---|"]
            for m in picks:
                lines.append(f"| {m['symbol']} | {m['direction']} | {m['score']} | {m['entry']} | "
                             f"{m['sl']} | {m['tgt']} | {m['qty']} |")
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
    if os.environ.get("GITHUB_ACTIONS") == "true":
        log("      GitHub Actions run — commit+push workflow sambhalega, Pages pe live hoga.")
    else:
        log("      Local run — browser me khol raha hoon...")
        try:
            webbrowser.open("file://" + os.path.abspath(plan_html))
        except Exception:
            pass
    log("      Reminder: history.csv 2 hafte me audit karo — asli teacher wahi hai. "
        "SHORT rows dekho: girta bazaar me paisa banta hai.")

if __name__ == "__main__":
    main()
