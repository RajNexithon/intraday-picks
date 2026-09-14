# intraday.py — DAILY TOP-10 INTRADAY PICKS (dynamic universe, auto data)
# Setup: pip install requests
# Locally: python intraday.py   |   GitHub Actions: auto, 8 PM IST Mon-Fri
# Run window: 7 PM ke baad kabhi bhi — bhavcopy patch ki wajah se 4 AM bhi chalega

import os, csv, io, time, requests, webbrowser
from datetime import datetime, timezone, timedelta, time as dtime
from pathlib import Path

# ---------- settings (env se override ho sakti hain) ----------
CAPITAL = int(os.environ.get("CAPITAL", "150000"))
RISK    = int(os.environ.get("RISK",    "400"))
RR      = float(os.environ.get("RR",    "2"))
LEV     = int(os.environ.get("LEV",     "5"))
TOP_N   = int(os.environ.get("TOP_N",   "10"))
MIN_SC  = int(os.environ.get("MIN_SC",  "7"))   # score /12
MAX_PER_SECTOR = 3                               # diversification
# ---------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
S = requests.Session(); S.headers.update(UA)
TICK = lambda x: round(round(x/0.05)*0.05, 2)

# FALLBACK — sirf tab use hota hai jab NSE ki site cloud/server se block ho
FALLBACK = [
 "HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK","BANDHANBNK","FEDERALBNK",
 "IDFCFIRSTB","PNB","BANKBARODA","CANBK","UNIONBANK","AUBANK","BAJFINANCE","BAJAJFINSV",
 "JIOFIN","SBILIFE","HDFCLIFE","ICICIPRULI","ICICIGI","SBICARD","SHRIRAMFIN","CHOLAFIN",
 "LICHSGFIN","MUTHOOTFIN","PFC","RECLTD","IRFC","HDFCAMC",
 "TCS","INFY","WIPRO","HCLTECH","TECHM","LTIM","PERSISTENT","COFORGE","MPHASIS","LTTS","KPITTECH","TATAELXSI",
 "MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","EICHERMOT","HEROMOTOCO","TVSMOTOR","ASHOKLEY",
 "MOTHERSON","BOSCHLTD","UNOMINDA","SONACOMS","EXIDEIND","ENDURANCE",
 "TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","JINDALSTEL","NATIONALUM","HINDZINC","APLAPOLLO","COALINDIA",
 "RELIANCE","ONGC","IOC","BPCL","GAIL","OIL","NTPC","POWERGRID","TATAPOWER","ADANIGREEN",
 "JSWENERGY","NHPC","SUZLON","CGPOWER","INOXWIND","WAAREEENER","PREMIERENE",
 "LT","SIEMENS","ABB","BHEL","HAL","BEL","MAZDOCK","COCHINSHIP","RVNL","IRCON","TITAGARH",
 "KEI","POLYCAB","HAVELLS","VOLTAS","CUMMINSIND","THERMAX","KAYNES","AMBER",
 "ITC","HINDUNILVR","NESTLEIND","BRITANNIA","DABUR","MARICO","GODREJCP","TATACONSUM","VBL","UBL",
 "UNITDSPR","JUBLFOOD","DEVYANI","TRENT","DMART","ZOMATO","POLICYBZR","IRCTC","TITAN","PAGEIND",
 "SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","APOLLOHOSP","LUPIN","AUROPHARMA","TORNTPHARM",
 "ALKEM","GLENMARK","BIOCON","ZYDUSLIFE","MANKIND","LAURUSLABS","GRANULES",
 "ULTRACEMCO","GRASIM","AMBUJACEM","ACC","DALBHARAT","SHREECEM","RAMCOCEM",
 "PIDILITIND","ASIANPAINT","BERGEPAINT","SRF","DEEPAKNTR","UPL","PIIND","TATACHEM",
 "ADANIENT","ADANIPORTS","DLF","GODREJPROP","OBEROIRLTY","LODHA","INDIGO","ABFRL","INDHOTEL","CHALET",
]

# ---------------- 1) DYNAMIC UNIVERSE ----------------
def nifty500_universe():
    """NSE ki official Nifty-500 list — symbol -> industry. Fail = {}"""
    for host in ("nsearchives.nseindia.com", "archives.nseindia.com"):
        try:
            r = S.get(f"https://{host}/content/indices/ind_nifty500list.csv", timeout=15)
            if r.ok and "Symbol" in r.text[:600]:
                out = {}
                for row in csv.DictReader(io.StringIO(r.text)):
                    sym = (row.get("Symbol") or "").strip()
                    ind = (row.get("Industry") or "OTHER").strip() or "OTHER"
                    if sym: out[sym] = ind
                if len(out) > 300: return out
        except Exception: pass
    return {}

def bhav_top():
    """Latest bhavcopy se EQ stocks ka FULL OHLCV + delivery (OFFICIAL NSE data).
    Ye hi last candle ka source-of-truth hoga — Yahoo lag ki dawa.
    Returns ({sym: dict(o,h,l,c,v,deliv,turn)}, date) ya ({}, None)"""
    d = datetime.now(IST).date()
    for back in range(6):                       # holiday/backfill handle
        day = d - timedelta(days=back)
        if day.weekday() >= 5: continue
        dmy = day.strftime("%d%m%Y")
        for host in ("nsearchives.nseindia.com", "archives.nseindia.com"):
            try:
                r = S.get(f"https://{host}/products/content/sec_bhavdata_full_{dmy}.csv", timeout=15)
                if not (r.ok and "SYMBOL" in r.text[:300].upper()): continue
                out = {}
                for row in csv.DictReader(io.StringIO(r.text)):
                    row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                    if row.get("SERIES") != "EQ": continue
                    try:
                        o = float(row["OPEN_PRICE"]); h = float(row["HIGH_PRICE"])
                        l = float(row["LOW_PRICE"]);  c = float(row["CLOSE_PRICE"])
                        v = float(row["TTL_TRD_QNTY"])
                        dv = float(row.get("DELIV_PER") or 0)
                    except Exception: continue
                    if c >= 50 and o > 0 and c*v >= 5e7:   # ₹5cr+ turnover, penny filter
                        out[row["SYMBOL"]] = dict(o=o, h=h, l=l, c=c, v=v, deliv=dv, turn=c*v)
                if out: return out, day
            except Exception: pass
    return {}, None

# ---------------- 2) DATA ----------------
def fetch(sym):
    """Yahoo se 6-mahine ki daily history. Sirf HISTORY ke liye —
    aaj/fresh candle ki zimmedari bhavcopy patch ki hai."""
    for _ in range(2):
        try:
            r = S.get("https://query1.finance.yahoo.com/v8/finance/chart/"
                      f"{requests.utils.quote(sym)}.NS",
                      params={"interval": "1d", "range": "6mo"}, timeout=10).json()
            res = r["chart"]["result"][0]; q = res["indicators"]["quote"][0]
            rows = []
            for i, t in enumerate(res["timestamp"]):
                o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
                if o and h and l and c and v:
                    rows.append((datetime.fromtimestamp(t, IST).date(), o, h, l, c, v))
            if len(rows) >= 26:
                if any((r[2]-r[3])/r[4] > 0.30 for r in rows[-20:]):  # corrupt/split guard
                    return None
                return rows
        except Exception: pass
        time.sleep(1)
    return None

# ---------------- 3) ANALYSIS (score /12) ----------------
def analyze(sym, rows, ind, deliv, nf20):
    d0, o, h, l, c, v = rows[-1]; P = rows[:-1]
    hi20  = max(r[2] for r in P[-20:])
    avgv  = sum(r[5] for r in P[-20:])/20
    if avgv <= 0: return None
    trs   = [max(a[2]-a[3], abs(a[2]-b[4]), abs(a[3]-b[4])) for a, b in zip(P[-15:-1], P[-14:])]
    atr   = sum(trs)/len(trs); atrp = atr/c*100
    sma20 = sum(r[4] for r in P[-20:])/20
    sma_o = sum(r[4] for r in P[-25:-5])/20
    ret20 = (c/P[-21][4]-1)*100 if len(P) >= 21 else 0
    rng = h-l; sc = 0; why = []

    if   c > hi20:        sc += 3; why.append("20D BREAKOUT")
    elif c >= hi20*0.995: sc += 2; why.append("20D HIGH KE PAAS")
    else: return None
    if c > hi20*1.05: return None                      # over-extended — chase nahi
    vx = v/avgv
    if   vx > 1.8: sc += 2; why.append(f"VOL {vx:.1f}x")
    elif vx > 1.3: sc += 1; why.append(f"VOL {vx:.1f}x")
    if c > o:                       sc += 1; why.append("GREEN")
    if rng and c >= l + rng*0.7:    sc += 1; why.append("STRONG CLOSE")
    if c > sma20 and sma20 >= sma_o:sc += 2; why.append("UPTREND")
    elif c > sma20:                 sc += 1
    if nf20 is not None and ret20 > nf20 + 3:
        sc += 1; why.append(f"RS +{ret20-nf20:.0f}% vs NIFTY")   # market se strong
    if 1.0 <= atrp <= 5.0: sc += 1
    elif atrp < 0.6: return None                       # dead stock
    if deliv and deliv >= 40: sc += 1; why.append(f"DELIV {deliv:.0f}%")

    entry = TICK(max(h*1.001, c*1.0005))
    sl    = TICK(entry - 0.7*atr)
    rps   = entry - sl
    if rps < entry*0.004: return None                  # SL bahut tight = wick hunt
    tgt = TICK(entry + RR*rps)
    qty = min(int(RISK/rps), int(CAPITAL*LEV/entry))
    if qty < 1: return None
    return dict(sym=sym, ind=ind, c=c, e=entry, sl=sl, t=tgt, q=qty,
                r=qty*rps, g=qty*rps*RR, sc=sc, vx=vx, why=" + ".join(why))

# ---------------- 4) HTML REPORT ----------------
def build_html(picks, sig, plan_d, gen, src, uni_n, strong_market):
    rows = "".join(
        f"<tr><td>{i+1}</td><td><b>{p['sym']}</b></td><td class=why>{p['ind']}</td><td>{p['c']:.2f}</td>"
        f"<td>{p['e']:.2f}</td><td class=sl>{p['sl']:.2f}</td><td class=tg>{p['t']:.2f}</td><td>{p['q']}</td>"
        f"<td>₹{p['r']:.0f}</td><td class=tg>₹{p['g']:.0f}</td><td class=sc>{p['sc']}/12</td>"
        f"<td class=why>{p['why']}</td></tr>" for i, p in enumerate(picks))
    if not rows:
        rows = "<tr><td colspan=12 style='text-align:left;color:#8d8875'>AAJ KOI QUALITY SETUP NHI BANA — NO-TRADE DAY. Force trade = loss.</td></tr>"
    banner = ("<div class=warn>MARKET WEAK — Nifty 20SMA ke neeche. Sirf score 9+ wale trade karo, size aadha rakho.</div>"
              if not strong_market else "")
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Aaj ke intraday picks</title>
<style>body{{background:#0d0c08;color:#ece7d8;font-family:Consolas,'Courier New',monospace;margin:0;padding:28px}}
h1{{font-size:20px;letter-spacing:.12em}}h1 span{{color:#ffb300}}
.meta{{color:#8d8875;font-size:11px;margin:6px 0 16px;letter-spacing:.06em}}
.warn{{max-width:1150px;border:1px solid #6b2020;background:#1a0d0d;color:#ff8d8d;font-size:12px;padding:9px 12px;margin-bottom:14px}}
table{{border-collapse:collapse;width:100%;max-width:1150px}}
th{{font-size:10px;color:#8d8875;text-transform:uppercase;letter-spacing:.1em;text-align:right;padding:8px 9px;border-bottom:1px solid #37331f}}
td{{padding:9px 9px;border-bottom:1px solid #26241b;text-align:right;font-size:13px}}
th:first-child,td:first-child{{text-align:left}}.sl{{color:#ff5d5d}}.tg{{color:#3ddc84}}.sc{{color:#ffb300;font-weight:700}}
.why{{color:#8d8875;font-size:10.5px;text-align:left}}
.rules{{max-width:1150px;margin-top:22px;font-size:12px;color:#cfc9b6;line-height:2}}.rules b{{color:#ffb300}}
.foot{{margin-top:16px;font-size:10px;color:#5b574a}}</style></head><body>
<h1>AAJ KE <span>{len(picks) or '0'} PICKS</span> — {plan_d.strftime('%a, %d %b')} ke liye</h1>
<div class=meta>SIGNAL: {sig.strftime('%d %b')} close · UNIVERSE: {uni_n} stocks ({src}) · CAPITAL ₹{CAPITAL:,} · RISK ₹{RISK}/trade · TARGET 1:{RR:g} (₹{RISK*RR:g}/winner) · GEN {gen}</div>
{banner}
<table><tr><th>#</th><th>Stock</th><th>Sector</th><th>Close</th><th>Entry</th><th>SL</th><th>Target</th><th>Qty</th><th>Risk</th><th>Gain@T</th><th>Score</th><th class=why>Kyun</th></tr>{rows}</table>
<div class=rules><b>KAL KE 5 RULES:</b><br>
1 &nbsp;Entry tabhi jab price ENTRY ke UPAR 5-min candle CLOSE kare. 0.7%+ gap-up ho to SKIP.<br>
2 &nbsp;Entry ke saath hi SL order daalo. SL hit = trade khatam.<br>
3 &nbsp;Max 3 trades. 2 winner = ₹{RISK*RR*2:g} → target poora → band.<br>
4 &nbsp;3:00 PM ke baad naya entry nahi. &nbsp; 5 &nbsp;Result/RBI/expiry day pe size aadha.</div>
<div class=foot>Data: NSE universe + bhavcopy (source of truth) + Yahoo history · Educational — market risk apna · {gen}</div></body></html>"""

# ---------------- MAIN ----------------
def main():
    t0 = time.time()
    pairs = nifty500_universe()                      # sym -> industry
    bhav, bdate = bhav_top()                         # OFFICIAL OHLCV + delivery
    if bhav:
        ranked = sorted(bhav.items(), key=lambda kv: -kv[1]["turn"])[:130]
        syms = [(s, pairs.get(s, "OTHER")) for s, _ in ranked]
        src = f"NSE bhavcopy {bdate:%d %b}, top turnover" if bdate else "NSE top turnover"
    else:
        base = [s for s in FALLBACK if not pairs or s in pairs] or FALLBACK
        syms = [(s, pairs.get(s, "OTHER")) for s in base]
        src = "fallback list" + (" ∩ NSE500" if pairs else "")
    print(f"Universe: {len(syms)} stocks ({src}). Scan shuru...")

    nf_rows = fetch("^NSEI")
    nf20 = None; strong = True
    if nf_rows:
        P = nf_rows[:-1]
        sma = sum(r[4] for r in P[-20:])/20
        nf20 = (nf_rows[-1][4]/P[-21][4]-1)*100 if len(P) >= 21 else None
        strong = nf_rows[-1][4] >= sma

    picks, sig = [], None; done = 0; patched = 0
    for sym, ind in syms:
        rows = fetch(sym); time.sleep(0.3); done += 1
        if done % 25 == 0: print(f"  ...{done}/{len(syms)} scanned, {len(picks)} picks ab tak")
        if not rows: continue

        # ---- BHAVCOPY PATCH: last candle official NSE data se ----
        # Yahoo same din ka candle de = replace (official zyada accurate)
        # Yahoo purana/pichhde hue = append (Yahoo lag ki dawa)
        if bdate and sym in bhav:
            b = bhav[sym]
            if rows[-1][0] == bdate:
                rows[-1] = (bdate, b["o"], b["h"], b["l"], b["c"], b["v"])
            elif rows[-1][0] < bdate:
                rows = rows + [(bdate, b["o"], b["h"], b["l"], b["c"], b["v"])]
                patched += 1

        sig = sig or rows[-1][0]
        p = analyze(sym, rows, ind, bhav.get(sym, {}).get("deliv", 0), nf20)
        if p and p["sc"] >= MIN_SC: picks.append(p)

    if patched:
        src += f" | Yahoo {patched} stocks me pichhda tha — {bdate:%d %b} candle bhavcopy se patch ✓"
        print(f"\n[BHAVCOPY PATCH] {patched} stocks me Yahoo ka candle missing/stale tha — official data se joda.")

    picks.sort(key=lambda p: (-p["sc"], -p["vx"]))
    final, cnt = [], {}
    for p in picks:                                   # sector cap
        if cnt.get(p["ind"], 0) >= MAX_PER_SECTOR: continue
        cnt[p["ind"]] = cnt.get(p["ind"], 0) + 1; final.append(p)
        if len(final) >= TOP_N: break

    if not sig: sig = datetime.now(IST).date()

    # ---- PLAN-DATE: current time se decide, data se nahi ----
    now = datetime.now(IST)
    if now.time() < dtime(9, 15):                     # open se pehle chalaya
        plan_d = now.date()                           #   -> AAJ ka session
    else:                                             # din me ya close ke baad
        plan_d = now.date() + timedelta(days=1)       #   -> agla session
    while plan_d.weekday() >= 5:
        plan_d += timedelta(days=1)                   # weekend skip

    gen = now.strftime('%d %b %H:%M')

    print(f"\n{'STOCK':<12}{'SECTOR':<22}{'ENTRY':>9}{'SL':>9}{'TARGET':>9}{'QTY':>5}{'RISK':>7}{'GAIN@T':>8}  SCORE")
    for p in final:
        print(f"{p['sym']:<12}{p['ind'][:20]:<22}{p['e']:>9.2f}{p['sl']:>9.2f}{p['t']:>9.2f}"
              f"{p['q']:>5}{p['r']:>7.0f}{p['g']:>8.0f}  {p['sc']}/12")
    if not final: print("\nAaj koi setup nahi — no-trade day. Skip karna bhi trade hai.")

    Path("plan.html").write_text(
        build_html(final, sig, plan_d, gen, src, len(syms), strong), encoding="utf-8")

    with open("history.csv", "a", encoding="utf-8") as f:   # record-keeping
        for p in final:
            f.write(f"{sig},{p['sym']},{p['e']},{p['sl']},{p['t']},{p['q']},{p['sc']}\n")

    if not os.environ.get("GITHUB_ACTIONS"):
        webbrowser.open(Path("plan.html").resolve().as_uri())
    print(f"\nDone in {time.time()-t0:.0f}s — plan.html ready ({len(syms)} stocks scanned).")

if __name__ == "__main__":
    main()
