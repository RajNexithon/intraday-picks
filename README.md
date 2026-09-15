# DAILY INTRADAY PICKS — Auto Top-10 Stock Scanner (NSE Cash Only)

Fully-automatic EOD breakout scanner. Roz top-10 intraday picks deta hai —
Entry / SL / Target / Qty / Risk / Gain@Target / Score / Reason ke saath.

- **Zero cost, zero server, zero manual kaam** — GitHub Actions roz 8:00 PM IST pe chalta hai
- **EOD data only** — plan close ke baad banta hai, execute open ke baad hota hai
- Philosophy: 100% perfect strategy exist nahi karti. Edge hai — chhota fix risk
  (₹400/trade) + 1:2 RR + discipline (max 3 trades/day). ₹1,000/day target = 2 winners.

---

## FILES

| File | Kaam |
|---|---|
| `intraday.py` | Poora scanner — universe, data, signals, scoring, outputs |
| `.github/workflows/daily.yml` | Roz 8:00 PM IST auto-run + commit + push |
| `plan.html` | Dark terminal-style picks page (GitHub Pages pe live) |
| `picks.csv` | Aaj ke picks — roz overwrite, Excel-friendly (utf-8-sig) |
| `history.csv` | Saare din append — audit ke liye (kaunsa sector hit hota hai, kaunsa SL khata hai) |

---

## SETUP — GITHUB (10 min, one-time)

1. GitHub pe **new public repo** banao: `intraday-picks`
2. Files **drag-drop** karke upload karo (zip upload extract NAHI hota):
   - `intraday.py`, `requirements.txt`, `README.md`
   - `.github/workflows/daily.yml` — **.github folder Windows me hidden hota hai:**
     Explorer me **View > Show > Hidden items** ON karo
3. Repo **Settings → Pages** → Source: `Deploy from a branch` → Branch: `main`, Folder: `/(root)` → Save
4. **Actions tab** → "Daily Intraday Picks" → **Enable workflow** (agar button dikhe)
5. Manual test: Actions → Daily Intraday Picks → **Run workflow** → green tick ka wait karo
6. Live page: `https://<TUMHARA-USERNAME>.github.io/intraday-picks/plan.html`

Bas. Roz 8:00 PM IST (±15-30 min — GitHub cron ka delay normal hai) page apne aap update hoga.

---

## SETUP — LOCAL (optional, testing ke liye)

```bash
pip install requests
python intraday.py            # scan chalao, browser me page khud khulega
python intraday.py --selftest # bina network — synthetic data pe logic check
```

Local pe NSE bhavcopy aapke ghar ke IP se aayegi — delivery % data bhi milega
(cloud IPs ko NSE block kar deta hai, isliye GitHub Actions pe kabhi-kabhi
Yahoo-only degraded mode chalega — page pe orange note dikhega).

---

## SETTINGS (env-overridable)

| Env | Default | Matlab |
|---|---|---|
| `CAPITAL` | 150000 | Total capital (₹) |
| `RISK` | 400 | Risk per trade (₹) — fix, badhana nahi |
| `RR` | 2 | Reward:Risk (1:2) |
| `LEV` | 5 | MIS leverage cap |
| `TOP_N` | 10 | Kitne picks |
| `MIN_SC` | 7 | Minimum score to pass |
| `MAX_PER_SECTOR` | 3 | Sector diversification cap |
| `SCAN_LIMIT` | 0 | 0 = full scan; testing ke liye chhota number |
| `FORCE_TODAY` | — | `DDMMYYYY` — past date pe paper-run (weekend test) |

Workflow me env values `.github/workflows/daily.yml` ke `env:` block me hain — wahin edit karo.

---

## STRATEGY (EOD breakout, sab previous day close pe)

**Pass conditions:** 20D breakout (ya high ke 0.5% andar) + volume expansion
(1.3-1.8x of 20-day avg) + green + strong close (day ke 70% range ke upar) +
uptrend (close > 20SMA, SMA rising) + RS vs NIFTY (+3% over 20d) + delivery ≥ 40%

**Rejects:** over-extended (20D high se 5%+ upar), dead stock (ATR < 0.6%),
SL bahut tight (< 0.4%), penny (< ₹50), low turnover (< ₹5cr/day)

**Levels:**
- Entry = signal-day high ka +0.1% (kal ka breakout trigger)
- SL = entry − 0.7 × ATR(14) — ATR-adaptive, wick-hunt se bachav
- Target = entry + 2 × risk (1:2 fixed)
- Qty = min(RISK/risk-per-share, CAPITAL×5/entry)

**Scoring (/12):** 20D BREAKOUT=3, HIGH KE PAAS=2, VOL>1.8x=2, VOL>1.3x=1,
GREEN=1, STRONG CLOSE=1, UPTREND rising=2, UPTREND basic=1, RS>NIFTY=1,
ATR sweet (1-5%)=1, DELIV≥40%=1

---

## DATA PIPELINE

1. **Universe:** NSE Nifty-500 official CSV → fail hone pe built-in ~129-stock
   sector-diverse fallback list
2. **Liquidity filter:** NSE bhavcopy — EQ series, close ≥ ₹50, turnover ≥ ₹5cr,
   top-130 by turnover. Delivery % bhi yahin se.
3. **History:** Yahoo Finance chart API — 6 months daily candles
4. **Bhavcopy PATCH (critical):** Yahoo NSE candles late aate hain. Har stock ki
   history ka last candle bhavcopy ke official OHLCV se REPLACE/APPEND hota hai.
   Isse subah 4 AM ko bhi fresh signal milta hai.
5. **Market regime:** Nifty close vs 20SMA — neeche ho to red banner:
   "MARKET WEAK — sirf 9+ score, size aadha"

---

## KAL KE 5 EXECUTION RULES (page pe bhi print hote hain)

1. Entry tabhi jab price ENTRY ke **UPAR** 5-min candle CLOSE kare.
   0.7%+ gap-up = **SKIP** (chase nahi karna)
2. Entry ke saath hi **SL order** — bina SL entry nahi
3. **Max 3 trades/day**; 2 winner = target poora = **BAND** (overtrade = account killer)
4. **3:00 PM ke baad** koi naya entry nahi
5. Result/RBI/expiry day pe **size aadha**

---

## GOTCHAS (pehle se pata hain, system ne handle kar rakha hai)

- Browser se NSE live data impossible hai (CORS + no public API) — isliye EOD approach
- NSE archives cloud IPs ko block karti hai → fallback list + do host try + degraded mode
- GitHub cron 5-30 min late chal sakta hai — EOD system ke liye farak nahi padta
- No-trade days NORMAL hain — kabhi 3-4 picks, kabhi 0. Force trade = loss
- Same day dobara run karo to history.csv me us plan-date ke rows replace hote hain (dupes nahi)
- **history.csv 2 hafte baad audit karna — asli teacher wahi hai.** Kaunsa sector
  hit karta hai, kaunsa level SL khata hai, sab us file me likha aayega.

---

*Educational research tool — financial advice nahi. derivatives nahi, sirf cash.
Bina SL ke trade = gambling.*
