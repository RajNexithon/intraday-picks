#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  DAILY INTRADAY PICKS — Auto Top-10 Stock Scanner (NSE CASH)
# ------------------------------------------------------------
#  Philosophy : 100% perfect strategy exist nahi karti. Asli edge =
#               chhota fixed risk + 1:2 RR + discipline (max 3 trades/day).
#  Data       : EOD only — NSE bhavcopy (source-of-truth) + Yahoo 6mo candles.
#               Live data ki zaroorat NAHI — plan close ke baad banta hai,
#               execute open ke baad hota hai.
#  Run local  : python intraday.py               (browser apne aap khulega)
#  Selftest   : python intraday.py --selftest    (bina network, synthetic data)
#  GitHub     : roz 8:00 PM IST auto-run -> plan.html / picks.csv / history.csv
#               commit hoke GitHub Pages pe live ho jate hain.
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
import webbrowser
import datetime as dt
from collections import Counter
from urllib.parse import quote

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
SCAN_LIMIT     = int(os.environ.get("SCAN_LIMIT", "0"))     # 0 = poora scan; testing ke liye chhota rakho
FORCE_TODAY    = os.environ.get("FORCE_TODAY", "").strip()  # DDMMYYYY — paper-run / past-date test ke liye

SELFTEST = "--selftest" in sys.argv
OUTP     = "selftest_" if SELFTEST else ""     # selftest outputs ko real files se alag rakho

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

NSE_HOSTS   = ["https://nsearchives.nseindia.com", "https://www1.nseindia.com"]
YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})

WEAK_ONLY_SCORE = 9      # market-weak regime me sirf itna+ score
TOP_UNIVERSE    = 130    # liquidity filter ke baad itne stocks scan honge

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
    while d.weekday() >= 5:            # 5=Sat, 6=Sun
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

def http_get(url, referer=None, tries=2, timeout=15):
    """GET -> text ya None. Failure pe chup-chaap None (caller sambhalega)."""
    hdr = {"Referer": referer} if referer else {}
    for t in range(tries):
        try:
            r = SESSION.get(url, headers=hdr, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text
            if r.status_code == 429:               # rate-limit -> backoff
                time.sleep(2 * (t + 1))
                continue
        except requests.RequestException:
            pass
        time.sleep(0.8 * (t + 1))
    return None

def http_get_json(url, referer=None, tries=2, timeout=15):
    txt = http_get(url, referer=referer, tries=tries, timeout=timeout)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except ValueError:
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

# Built-in fallback: ~129 sector-diverse liquid large/mid caps.
# NSE archives cloud IPs ko 403 maar deti hai — tab yehi list kaam aati hai.
_FALLBACK_RAW = """
BANK: HDFCBANK ICICIBANK SBIN KOTAKBANK AXISBANK INDUSINDBK PNB FEDERALBNK
FINANCE: BAJFINANCE BAJAJFINSV JIOFIN CHOLAFIN PFC RECLTD IRFC LTF
INSUR: SBILIFE HDFCLIFE ICICIPRULI
CAPMKT: BSE MCX CDSL CAMS
IT: TCS INFY HCLTECH WIPRO TECHM LTIM PERSISTENT COFORGE
PHARMA: SUNPHARMA DRREDDY CIPLA DIVISLAB AUROPHARMA ZYDUSLIFE TORNTPHARM
HOSP: APOLLOHOSP MAXHEALTH FORTIS LALPATHLAB
AUTO: MARUTI M&M TATAMOTORS BAJAJ-AUTO EICHERMOT HEROMOTOCO
AUTOANC: MOTHERSON BOSCHLTD BALKRISIND APOLLOTYRE CEATLTD SONACOMS
FMCG: HINDUNILVR ITC NESTLEIND BRITANNIA MARICO TATACONSUM VBL
OILGAS: RELIANCE ONGC IOC BPCL GAIL OIL
POWER: NTPC POWERGRID TATAPOWER ADANIPOWER JSWENERGY CGPOWER
METAL: TATASTEEL JSWSTEEL HINDALCO VEDL JINDALSTEL NMDC
CEMENT: ULTRACEMCO GRASIM SHREECEM AMBUJACEM ACC DALBHARAT
INFRA: LT RVNL CONCOR
CAPGOODS: SIEMENS ABB BHEL CUMMINSIND POLYCAB HAVELLS
DEFENCE: HAL BEL MAZDOCK COCHINSHIP GRSE TITAGARH
CONSDUR: TITAN DIXON VOLTAS BLUESTARCO CROMPTON
CHEM: PIDILITIND SRF AARTIIND DEEPAKNTR TATACHEM PIIND
TELECOM: BHARTIARTL INDUSTOWER TATACOMM
REALTY: DLF GODREJPROP OBEROIRLTY LODHA
RETAIL: TRENT ABFRL PAGEIND
NEWAGE: ETERNAL PAYTM POLICYBZR
MEDIA: ZEEL NAUKRI
TRAVEL: INDIGO IRCTC
LOGISTICS: DELHIVERY
"""
FALLBACK_UNIVERSE = {}
for _line in _FALLBACK_RAW.strip().splitlines():
    _p = _line.split(":")
    for _s in _p[1].split():
        FALLBACK_UNIVERSE[_s.strip().upper()] = _p[0].strip()

def load_universe():
    """Nifty-500 official CSV -> {sym: sector}. Fail -> built-in fallback."""
    for host in NSE_HOSTS:
        txt = http_get(host + "/content/indices/ind_nifty500list.csv",
                       referer="https://www.nseindia.com/", tries=2)
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

# ================= BHAVCOPY (source-of-truth) =================
def fetch_bhavcopy():
    """Aaj se peeche max 10 trading din try karo. -> ({sym: row}, date) ya (None, None)."""
    d = today_ist().date()
    for back in range(10):
        cur = d - dt.timedelta(days=back)
        if cur.weekday() >= 5:
            continue
        fname = cur.strftime("%d%m%Y")
        for host in NSE_HOSTS:
            txt = http_get(f"{host}/products/content/sec_bhavdata_full_{fname}.csv",
                           referer="https://www.nseindia.com/", tries=2)
            if not txt:
                continue
            try:
                rows = list(csv.DictReader(io.StringIO(txt), skipinitialspace=True))
            except Exception:
                continue
            out = {}
            for r in rows:
                sym = (r.get("SYMBOL") or "").strip().upper()
                ser = (r.get("SERIES") or "").strip().upper()
                if not sym or ser != "EQ":
                    continue
                def num(k):
                    v = (r.get(k) or "").strip().replace(",", "")
                    try:
                        return float(v)
                    except ValueError:
                        return 0.0
                dv = (r.get("DELIV_PER") or "").strip()
                try:
                    deliv = float(dv)
                except ValueError:
                    deliv = 0.0        # "-" ya blank -> 0
                out[sym] = {"date": cur,
                            "open": num("OPEN_PRICE"), "high": num("HIGH_PRICE"),
                            "low": num("LOW_PRICE"), "close": num("CLOSE_PRICE"),
                            "prev_close": num("PREV_CLOSE"),
                            "volume": num("TTL_TRD_QNTY"),
                            "turnover": num("TURNOVER_LACS"),   # Rs lakh me
                            "deliv": deliv}
            if len(out) > 500:                         # sane file check
                return out, cur
    return None, None

def liquidity_top(bhav, uni, n=TOP_UNIVERSE):
    """EQ + close>=50 + turnover>=5cr (500 lacs) -> top-n by turnover, universe ke andar."""
    cand = []
    for sym, row in bhav.items():
        if row["close"] < 50 or row["turnover"] < 500:
            continue
        if sym not in uni:
            continue
        cand.append(sym)
    cand.sort(key=lambda s: -bhav[s]["turnover"])
    if SCAN_LIMIT > 0:
        cand = cand[:SCAN_LIMIT]
    return cand[:n]

# ================= YAHOO HISTORY =================
def yahoo_history(symbol, rng="6mo"):
    """6mo daily candles -> [{date,open,high,low,close,volume}] ya None. Dono host try.
    Stock me .NS suffix lagta hai; index (^NSEI) ko raw hi rehne dete hain."""
    raw = symbol if symbol.startswith("^") else f"{symbol}.NS"
    path = quote(raw, safe="")
    for host in YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{path}?interval=1d&range={rng}"
        j = http_get_json(url, tries=2, timeout=12)
        if not j:
            continue
        try:
            res = j["chart"]["result"][0]
            ts = res["timestamp"]
            q0 = res["indicators"]["quote"][0]
            o, h, l, c, v = (q0["open"], q0["high"], q0["low"], q0["close"], q0["volume"])
        except (KeyError, IndexError, TypeError):
            continue
        out = []
        for i, t in enumerate(ts):
            try:
                if o[i] is None or h[i] is None or l[i] is None or c[i] is None or h[i] <= 0:
                    continue
                out.append({"date": dt.datetime.fromtimestamp(t, IST).date(),
                            "open": float(o[i]), "high": float(h[i]),
                            "low": float(l[i]), "close": float(c[i]),
                            "volume": float(v[i] or 0)})
            except (IndexError, TypeError, ValueError):
                continue
        if len(out) >= 25:
            return out
    return None

def patch_with_bhavcopy(hist, row):
    """CRITICAL FIX: Yahoo NSE candles late aate hain.
    - bhavcopy date == yahoo last date  -> last candle REPLACE (official OHLCV)
    - bhavcopy date >  yahoo last date  -> candle APPEND
    -> bool (patch hua ya nahi)"""
    last = hist[-1]
    bc = {"date": row["date"], "open": row["open"], "high": row["high"],
          "low": row["low"], "close": row["close"], "volume": row["volume"]}
    if row["date"] == last["date"]:
        hist[-1] = bc
        return True
    if row["date"] > last["date"]:
        hist.append(bc)
        return True
    return False

# ================= INDICATORS + ANALYSIS =================
def atr14(hist, i, n=14):
    """Wilder ATR — seedha SMA seed, phir Wilder smoothing."""
    if i < n:
        return None
    trs = []
    for j in range(1, i + 1):
        h, l = hist[j]["high"], hist[j]["low"]
        pc = hist[j - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr

def analyze(sym, sector, hist, brow, nret20):
    """Sab indicators + rejects + score + levels. -> (metrics dict) ya (None, reject_reason)."""
    i = len(hist) - 1
    if i < 24:
        return None, "DATA<25d"
    today = hist[i]
    closes = [c["close"] for c in hist]
    highs  = [c["high"] for c in hist]
    vols   = [c["volume"] for c in hist]

    close = today["close"]
    if close < 50:
        return None, "PENNY(<50)"
    if brow is None:                       # bhavcopy nahi hai -> turnover estimate se filter
        if (close * today["volume"]) / 1e5 < 500:
            return None, "ILLIQUID(est)"

    prev20_high = max(highs[i - 20:i])
    vol_avg20   = sum(vols[i - 20:i]) / 20.0
    sma20       = sum(closes[i - 19:i + 1]) / 20.0
    sma20_old   = sum(closes[i - 24:i - 4]) / 20.0
    ret20       = closes[i] / closes[i - 20] - 1.0 if closes[i - 20] > 0 else 0.0

    atr = atr14(hist, i)
    if not atr or atr <= 0:
        return None, "ATR-ERR"
    atr_pct = atr / close
    if atr_pct < 0.006:                    # dead stock — paisa panjhi me
        return None, "DEAD(ATR<0.6%)"

    vol_x    = today["volume"] / vol_avg20 if vol_avg20 > 0 else 0.0
    rng      = today["high"] - today["low"]
    strong   = rng > 0 and (close - today["low"]) / rng >= 0.70
    green    = close > today["open"]
    breakout = close > prev20_high
    near_high = (not breakout) and close >= prev20_high * 0.995
    up_basic  = close > sma20
    up_rising = up_basic and sma20 > sma20_old

    # ---- REJECTS (pehle kaat do, score baad me) ----
    if close >= prev20_high * 1.05:        # over-extended — chase mat karo
        return None, "OVEREXT(>5%)"
    entry = round(today["high"] * 1.001, 2)     # aaj ke high ke 0.1% upar = kal ka trigger
    rps   = 0.7 * atr                            # risk per share (ATR-adaptive SL)
    if rps / entry < 0.004:                      # SL bahut tight — wick-hunt pakwayega
        return None, "SL-THIN(<0.4%)"
    qty = min(int(RISK // rps), int((CAPITAL * LEV) // entry))
    if qty < 1:
        return None, "QTY-0"

    deliv = brow["deliv"] if brow else 0.0
    rs_ok = (nret20 is not None) and (ret20 - nret20) >= 0.03

    # ---- SCORING (/12) ----
    sc = 0
    tags = []
    if breakout:
        sc += 3; tags.append("20D BREAKOUT")
    elif near_high:
        sc += 2; tags.append("HIGH KE PAAS")
    if vol_x >= 1.8:
        sc += 2; tags.append(f"VOL {vol_x:.1f}x")
    elif vol_x >= 1.3:
        sc += 1; tags.append(f"VOL {vol_x:.1f}x")
    if green:
        sc += 1; tags.append("GREEN")
    if strong:
        sc += 1; tags.append("STRONG CLOSE")
    if up_rising:
        sc += 2; tags.append("UPTREND+")
    elif up_basic:
        sc += 1; tags.append("UPTREND")
    if rs_ok:
        sc += 1; tags.append("RS>NIFTY")
    if 0.01 <= atr_pct <= 0.05:
        sc += 1; tags.append("ATR SWEET")
    if deliv >= 40:
        sc += 1; tags.append(f"DELIV {deliv:.0f}%")

    sl   = round(entry - rps, 2)
    tgt  = round(entry + RR * rps, 2)
    risk_amt = round(qty * rps, 2)
    gain     = round(qty * RR * rps, 2)

    return {"symbol": sym, "sector": sector, "score": sc,
            "entry": entry, "sl": sl, "tgt": tgt, "qty": qty,
            "risk": risk_amt, "gain": gain,
            "atr_pct": atr_pct * 100, "vol_x": vol_x, "deliv": deliv,
            "close": close, "volume": today["volume"],
            "signal_date": today["date"], "tags": tags,
            "turnover": brow["turnover"] if brow else (close * today["volume"]) / 1e5}, None

# ================= PICK FINALIZATION =================
def final_picks(passing):
    """Score+volume sort, sector cap lagao, TOP_N lo."""
    passing.sort(key=lambda m: (-m["score"], -m["volume"]))
    picked, sec_cnt = [], Counter()
    for m in passing:
        if len(picked) >= TOP_N:
            break
        if sec_cnt[m["sector"]] >= MAX_PER_SECTOR:
            continue
        picked.append(m)
        sec_cnt[m["sector"]] += 1
    return picked

def regime_from_hist(hist):
    """Nifty close vs 20SMA + 20d return. -> dict ya None"""
    if not hist or len(hist) < 25:
        return None
    i = len(hist) - 1
    closes = [c["close"] for c in hist]
    sma20 = sum(closes[i - 19:i + 1]) / 20.0
    ret20 = closes[i] / closes[i - 20] - 1.0 if closes[i - 20] > 0 else 0.0
    return {"close": closes[i], "sma20": sma20, "ret20": ret20,
            "weak": closes[i] < sma20, "date": hist[i]["date"]}

def nifty_regime():
    return regime_from_hist(yahoo_history("^NSEI"))

# ================= OUTPUT: CONSOLE =================
def print_console_table(picks, plan_day):
    W = 108
    print()
    print("=" * W)
    print(f" AAJ KE {len(picks)} PICKS — {plan_day.strftime('%a, %d %b %Y')} ke liye")
    print("=" * W)
    if not picks:
        print(" KOI PICK NAHI — no-trade day bhi ek position hai. Cash sitting = capital safe.")
    else:
        print(f"{'#':<3}{'SYMBOL':<11}{'SEC':<9}{'SC':>3}{'ENTRY':>10}{'SL':>10}{'TARGET':>10}"
              f"{'QTY':>7}{'RISK Rs':>10}{'GAIN Rs':>10}  REASON")
        print("-" * W)
        for i, m in enumerate(picks, 1):
            print(f"{i:<3}{m['symbol']:<11}{m['sector']:<9}{m['score']:>3}{m['entry']:>10.2f}"
                  f"{m['sl']:>10.2f}{m['tgt']:>10.2f}{m['qty']:>7}{m['risk']:>10.0f}"
                  f"{m['gain']:>10.0f}  {' | '.join(m['tags'])}")
    print("=" * W)

# ================= OUTPUT: CSV =================
HEADER = ["PLAN_DATE", "SIGNAL_DATE", "SYMBOL", "SECTOR", "SCORE", "ENTRY", "SL", "TARGET",
          "QTY", "RISK_RS", "GAIN_RS", "ATR_PCT", "VOL_X", "DELIV_PCT", "CLOSE",
          "REGIME", "REASON", "RUN_TS"]

def csv_rows(picks, plan_day, regime, run_ts):
    reg = ("WEAK" if regime and regime["weak"] else "OK") if regime else "NA"
    out = []
    for m in picks:
        out.append([plan_day.isoformat(), m["signal_date"].isoformat(), m["symbol"],
                    m["sector"], m["score"], m["entry"], m["sl"], m["tgt"], m["qty"],
                    m["risk"], m["gain"], round(m["atr_pct"], 2), round(m["vol_x"], 2),
                    round(m["deliv"], 1), m["close"], reg,
                    " | ".join(m["tags"]), run_ts])
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
                next(rd, None)                       # header
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
.wrap{max-width:1200px;margin:0 auto}
h1{color:var(--amber);font-size:21px;margin:0 0 6px;letter-spacing:.5px}
.meta{color:var(--dim);font-size:12.5px;line-height:1.8;margin-bottom:12px}
.patch{color:var(--green)}
.warn{color:#ff9d66}
.banner{border:1px solid var(--red);background:#2a0f0f;color:#ffb4ae;padding:10px 14px;
        margin:10px 0;font-weight:bold;font-size:13.5px;line-height:1.6}
.tablewrap{overflow-x:auto;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:1000px}
th{background:#171309;color:var(--amber);text-align:right;padding:9px 8px;
   border-bottom:2px solid var(--amber);white-space:nowrap;font-size:12px}
th.l,td.l{text-align:left}
td{padding:8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
tr:hover td{background:#1a160c}
.sym{color:var(--amber);font-weight:bold}
.sec{color:#c8b78a;font-size:12px}
.sc{color:var(--amber);font-weight:bold}
.sc9{color:#ffd54d;background:#2a230d;display:inline-block;padding:1px 8px;
     border:1px solid #6b5a1a;font-weight:bold}
.sl{color:var(--red)}
.tg{color:var(--green)}
.gn{color:var(--green);font-weight:bold}
.rsn{color:var(--dim);font-size:11.5px;text-align:left;white-space:normal;min-width:240px;line-height:1.55}
.rules{border:1px solid var(--line);background:var(--panel);padding:14px 18px;margin-top:18px}
.rules h2{color:var(--amber);font-size:15px;margin:0 0 8px}
.rules ol{margin:0;padding-left:22px;line-height:2;font-size:13px}
.empty{border:1px dashed #6b5a1a;color:var(--amber);padding:28px 20px;text-align:center;
       font-size:14px;margin-top:14px;line-height:1.9}
.foot{color:#6d664f;font-size:11px;margin-top:16px;line-height:1.8}
"""

def write_html(path, picks, plan_day, signal_date, uni_src, patched, scanned, regime, bhav_ok, run_ts, secs):
    weak = bool(regime and regime["weak"])
    day_s = plan_day.strftime("%a, %d %b %Y")
    sig_s = signal_date.strftime("%a, %d %b %Y") if signal_date else "NA"

    nifty_s = ""
    if regime:
        pos = "UPAR" if not weak else "NEECHE"
        nifty_s = f" | NIFTY {regime['close']:,.0f} &bull; 20SMA({regime['sma20']:,.0f}) ke {pos}"

    if bhav_ok:
        patch_s = (f' <span class="patch">| candle bhavcopy se patch ✓ ({patched}/{scanned})</span>'
                   if patched > 0 else "")
    else:
        patch_s = (' <span class="warn">| BHAVCOPY fail &rarr; Yahoo-only mode '
                   '(delivery data nahi, max score 11)</span>')

    meta = (f"Signal: {sig_s} (close) | Universe: {scanned} liquid stocks ({uni_src}) | "
            f"Capital: ₹{inr(CAPITAL)} | Risk: ₹{inr(RISK)}/trade | RR: 1:{RR:g} | "
            f"Lev: {LEV:g}x MIS{nifty_s}{patch_s}")

    banner = ""
    if weak:
        banner = ('<div class="banner">MARKET WEAK — NIFTY 20SMA ke NEECHE. '
                  'Sirf 9+ score wale picks dikhaye ja rahe hain. Size AADHA karo. '
                  'Gap-up chase mat karo.</div>')

    if picks:
        rows = []
        for i, m in enumerate(picks, 1):
            sc_cls = "sc9" if m["score"] >= 9 else "sc"
            rows.append(
                f"<tr><td>{i}</td>"
                f'<td class="l sym">{html.escape(m["symbol"])}</td>'
                f'<td class="l sec">{html.escape(m["sector"])}</td>'
                f'<td><span class="{sc_cls}">{m["score"]}</span></td>'
                f'<td>{m["entry"]:.2f}</td>'
                f'<td class="sl">{m["sl"]:.2f}</td>'
                f'<td class="tg">{m["tgt"]:.2f}</td>'
                f'<td>{m["qty"]}</td>'
                f'<td>₹{inr(m["risk"])}</td>'
                f'<td class="gn">₹{inr(m["gain"])}</td>'
                f'<td>{m["atr_pct"]:.1f}%</td>'
                f'<td class="rsn">{html.escape(" · ".join(m["tags"]))}</td></tr>')
        table = ('<div class="tablewrap"><table>'
                 '<tr><th>#</th><th class="l">SYMBOL</th><th class="l">SECTOR</th><th>SCORE</th>'
                 '<th>ENTRY</th><th>SL</th><th>TARGET</th><th>QTY</th><th>RISK ₹</th>'
                 '<th>GAIN @TGT</th><th>ATR%</th><th class="l">REASON (score breakdown)</th></tr>'
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
<title>Daily Intraday Picks — NSE Cash</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
<h1>AAJ KE {len(picks)} PICKS — {day_s} ke liye</h1>
<div class="meta">{meta}</div>
{banner}
{table}
<div class="rules"><h2>KAL KE 5 RULES ({day_s} ke liye)</h2><ol>
<li>Entry tabhi jab price ENTRY ke <b>UPAR</b> 5-min candle CLOSE kare. 0.7%+ gap-up = <b>SKIP</b> (chase nahi karna).</li>
<li>Entry ke saath hi <b>SL order</b> — bina SL entry nahi.</li>
<li><b>Max 3 trades/day</b>; 2 winner = target poora = <b>BAND</b> (overtrade = account killer).</li>
<li><b>3:00 PM ke baad</b> koi naya entry nahi.</li>
<li>Result/RBI/expiry day pe <b>size aadha</b>.</li>
</ol></div>
<div class="foot">Data: NSE bhavcopy (EOD, source-of-truth) + Yahoo Finance 6mo daily | Auto-run: GitHub Actions, 8:00 PM IST (Mon-Fri)<br>
Educational research note — financial advice NAHI. Bina SL trade = gambling.<br>
Generated: {run_ts} | Runtime: {secs:.0f}s</div>
</div></body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)

# ================= SELFTEST (synthetic data — network ZERO) =================
HISTS = {}   # selftest me synthetic candles yahan

def _synth_days(n=130):
    d = today_ist().date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))

def build_synth():
    """60 fake stocks + fake bhavcopy + fake nifty. Reject paths bhi cover hote hain:
    TEST01 penny, TEST02 dead, TEST03 over-extended."""
    days = _synth_days()
    secs = ["IT", "BANK", "PHARMA", "AUTO", "FMCG", "METAL", "POWER", "FINANCE", "CHEM", "CONSDUR"]
    uni, hist_map, bhav = {}, {}, {}
    for k in range(60):
        sym = f"TEST{k:02d}"
        uni[sym] = secs[k % len(secs)]
        rng = random.Random(sym)
        style = k % 10
        price = 90 + rng.random() * 800
        if style == 1:
            price = 32 + rng.random() * 10          # penny zone (<50)
        candles = []
        for d in days[:-1]:
            if style == 2:
                vol, drift = 0.004, 0.0002          # dead — ATR<0.6%
            elif style in (7, 8, 9):
                vol, drift = 0.016, 0.0010          # strong uptrend
            else:
                vol, drift = 0.012, 0.0004          # normal
            o = price
            c = max(5.0, o * (1 + rng.gauss(drift, vol)))
            hi = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)) + 0.001)
            lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)) - 0.001)
            v = int(60000 + rng.random() * 250000)
            candles.append({"date": d, "open": round(o, 2), "high": round(hi, 2),
                            "low": round(lo, 2), "close": round(c, 2), "volume": v})
            price = c
        prev = candles[-1]
        avg_v = sum(c["volume"] for c in candles[-20:]) / 20.0
        if style == 3:                               # over-extended banao (+8%)
            o = prev["close"] * 1.001
            c = prev["close"] * 1.08
            hi = c * 1.002
            lo = o * 0.999
            v = int(avg_v * 2.0)
        elif style in (0, 5, 6, 7, 8, 9):            # breakout day
            o = prev["close"] * 1.001
            c = prev["close"] * (1.020 + rng.random() * 0.020)
            hi = c * 1.003
            lo = o * 0.998
            v = int(avg_v * (1.4 + rng.random() * 1.2))
        elif style == 4:                             # dull red day
            o = prev["close"] * 0.998
            c = prev["close"] * 0.994
            hi = o * 1.002
            lo = c * 0.997
            v = int(avg_v * 0.9)
        else:                                        # flat day
            o = prev["close"] * 1.0005
            c = prev["close"] * (1 + rng.gauss(0, 0.004))
            hi = max(o, c) * 1.002
            lo = min(o, c) * 0.998
            v = int(avg_v * 1.1)
        candles.append({"date": days[-1], "open": round(o, 2), "high": round(hi, 2),
                        "low": round(lo, 2), "close": round(c, 2), "volume": v})
        hist_map[sym] = candles
        bhav[sym] = {"date": days[-1], "open": o, "high": hi, "low": lo, "close": c,
                     "prev_close": prev["close"], "volume": v,
                     "turnover": c * v / 1e5, "deliv": 25 + rng.random() * 50}
    nf, p = [], 24000.0
    for j, d in enumerate(days):
        dr = -0.004 if (os.environ.get("SELFTEST_WEAK") and j >= len(days) - 25) else 0.0004
        p *= (1 + random.Random(d.toordinal() * 7).gauss(dr, 0.005))
        nf.append({"date": d, "open": p, "high": p, "low": p, "close": p, "volume": 0})
    return uni, hist_map, bhav, nf, days[-1]

def get_history(sym):
    if SELFTEST:
        return HISTS.get(sym)
    return yahoo_history(sym)

# ================= MAIN =================
def main():
    t0 = time.time()
    run_ts = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    plan_day = compute_plan_day()
    mode = "SELFTEST (synthetic data)" if SELFTEST else "LIVE"
    log("=" * 78)
    log(f" DAILY INTRADAY PICKS — NSE CASH ONLY | {mode}")
    log(f" Run: {run_ts}")
    log(f" CAPITAL=Rs {inr(CAPITAL)} | RISK=Rs {RISK:.0f} | RR=1:{RR:g} | LEV={LEV:g}x | "
        f"TOP_N={TOP_N} | MIN_SC={MIN_SC} | MAX/SECTOR={MAX_PER_SECTOR}")
    log(f" PLAN DAY: {plan_day.strftime('%a, %d %b %Y')}"
        + (f"  (FORCE_TODAY={FORCE_TODAY})" if FORCE_TODAY else ""))
    log("=" * 78)

    # ---- [1] data load ----
    if SELFTEST:
        uni, hist_map, bhav, nf, sig_d = build_synth()
        HISTS.clear()
        HISTS.update(hist_map)
        bhav_date, bhav_ok = sig_d, True
        uni_src = f"SYNTH ({len(uni)})"
        ranked = sorted(uni.keys())
        regime = regime_from_hist(nf)
        log(f"[1/5] Universe: {uni_src} | bhavcopy: synthetic {bhav_date}")
    else:
        uni, uni_src = load_universe()
        log(f"[1/5] Universe: {uni_src}")
        bhav, bhav_date = fetch_bhavcopy()
        bhav_ok = bhav is not None
        if bhav_ok:
            log(f"[2/5] Bhavcopy OK: {bhav_date} ({len(bhav)} EQ rows) — source-of-truth")
            ranked = liquidity_top(bhav, uni)
            log(f"      Liquidity filter: {len(ranked)} candidates "
                f"(EQ, close>=50, turnover>=5cr, top-{TOP_UNIVERSE})")
        else:
            log("[2/5] Bhavcopy FAIL (cloud-IP block ya holiday?) -> Yahoo-only degraded mode")
            ranked = sorted(uni.keys())
            if SCAN_LIMIT > 0:
                ranked = ranked[:SCAN_LIMIT]
            log(f"      Degraded scan list: {len(ranked)} stocks (delivery data nahi milega)")
        regime = nifty_regime()

    weak = bool(regime and regime["weak"])
    if regime:
        tag = "WEAK" if weak else "OK"
        log(f"[3/5] NIFTY {regime['close']:,.0f} vs 20SMA {regime['sma20']:,.0f} -> {tag}"
            + ("  (sirf 9+ score, size aadha)" if weak else ""))
    else:
        log("[3/5] NIFTY data nahi mila — regime NA (RS point skip hoga)")
    nret20 = regime["ret20"] if regime else None

    # ---- [2] scan ----
    log(f"[4/5] Scan shuru: {len(ranked)} stocks (Yahoo 6mo + bhavcopy patch)...")
    passing, skips = [], Counter()
    patched = ok_ct = fail_ct = consec_fail = 0
    failed_syms = []
    total = len(ranked)
    for n_, sym in enumerate(ranked, 1):
        sector = uni.get(sym, "OTHER")
        hist = get_history(sym)
        if not hist:
            fail_ct += 1
            failed_syms.append(sym)
            consec_fail += 1
            if consec_fail >= 8 and ok_ct == 0:
                log("  !! Yahoo se lagatar data nahi mil raha (network/block). Scan abort.")
                break
            continue
        consec_fail = 0
        ok_ct += 1
        brow = bhav.get(sym) if bhav else None
        if brow and patch_with_bhavcopy(hist, brow):
            patched += 1
        m, rej = analyze(sym, sector, hist, brow, nret20)
        if m is None:
            skips[rej] += 1
        else:
            need = WEAK_ONLY_SCORE if weak else MIN_SC
            if m["score"] < need:
                skips[f"SCORE<{need}"] += 1
            else:
                passing.append(m)
        if n_ % 25 == 0:
            log(f"      ... {n_}/{total}")
        if not SELFTEST:
            time.sleep(0.10)

    if ok_ct == 0 and fail_ct > 0 and not SELFTEST:
        log("\n!! KOI BHI stock ka data nahi mila — Yahoo block/down hai.")
        log("!! Outputs NAHI likhe (purane plan.html safe hai). Kuch der baad retry karo.")
        sys.exit(2)

    picks = final_picks(passing)

    # ---- [3] outputs ----
    signal_date = bhav_date or (regime["date"] if regime else plan_day)
    secs = time.time() - t0
    plan_html, picks_csv, hist_csv = f"{OUTP}plan.html", f"{OUTP}picks.csv", f"{OUTP}history.csv"

    rows = csv_rows(picks, plan_day, regime, run_ts)
    write_picks_csv(picks_csv, rows)
    write_history_csv(hist_csv, rows, plan_day)   # re-run safe: same plan-date replace hota hai
    write_html(plan_html, picks, plan_day, signal_date, uni_src, patched, len(ranked),
               regime, bhav_ok, run_ts, secs)

    log(f"[5/5] Scanned-OK: {ok_ct} | Patched: {patched} | Data-fail: {fail_ct} | "
        f"Pass: {len(passing)} | Picks: {len(picks)}")
    if failed_syms:
        log("      Data-fail symbols: " + ", ".join(failed_syms))
    if skips:
        log("      Skips: " + ", ".join(f"{k} x{v}" for k, v in skips.most_common(8)))

    print_console_table(picks, plan_day)

    log(f"      Files: {plan_html} | {picks_csv} | {hist_csv}  ({secs:.0f}s)")
    if not SELFTEST:
        p = os.environ.get("GITHUB_STEP_SUMMARY")
        if p:
            try:
                lines = [f"## Picks — {plan_day.strftime('%d %b %Y')} ke liye", "",
                         "| SYM | SC | ENTRY | SL | TGT | QTY |", "|---|---|---|---|---|---|"]
                for m in picks:
                    lines.append(f"| {m['symbol']} | {m['score']} | {m['entry']} | "
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
        log("      Reminder: history.csv 2 hafte me audit karo — asli teacher wahi hai.")
    else:
        log("      SELFTEST files (selftest_*) likhi gayi — real outputs untouched hain.")

if __name__ == "__main__":
    main()


