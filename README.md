# DAILY INTRADAY PICKS — Auto Top-10 Scanner (NSE Cash, LONG + SHORT)

Fully-automatic EOD scanner. Roz top-10 intraday picks deta hai — **dono direction me**:
LONG (breakout buys) + SHORT (breakdown sells) — Entry / SL / Target / Qty / Risk /
Gain@Target / Score / Reason ke saath. **FII/DII flows bhi page pe.**

- **Zero cost, zero server, zero manual kaam** — GitHub Actions roz 8:00 PM IST pe chalta hai
- **100% NSE/BSE data — Yahoo poori tarah HATA DIYA** (sirf official bhavcopy history)
- **MIN-5 GUARANTEE** — kam se kam 5 picks; score tiers relax hote hain (LOW CONF /
  WEAK PASS tag = kam confidence, aadha size ya skip better)
- Philosophy: 100% perfect strategy exist nahi karti. Edge hai — chhota fix risk
  (₹400/trade) + 1:2 RR + discipline (max 3 trades/day). ₹1,000/day target = 2 winners.

---

## FILES

| File | Kaam |
|---|---|
| `intraday.py` | Poora scanner — universe, bhavcopy history, LONG+SHORT signals, FII/DII, outputs |
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

Local pe NSE bhavcopy aapke ghar ke IP se aayegi — full data + delivery % milega.
(Cloud IPs — GitHub Actions included — ko NSE kabhi-kabhi 403 maar deta hai; tab
system khud BSE UDiFF bhavcopy pe switch ho jata hai, delivery % skip ho jata hai.
BSE bhi fail ho to script purane outputs ko touch NAHI karta, honest error deta hai.)

---

## SETTINGS (env-overridable)

| Env | Default | Matlab |
|---|---|---|
| `CAPITAL` | 150000 | Total capital (₹) |
| `RISK` | 400 | Risk per trade (₹) — fix, badhana nahi |
| `RR` | 2 | Reward:Risk (1:2) |
| `LEV` | 5 | MIS leverage cap |
| `TOP_N` | 10 | Kitne picks |
| `MIN_SC` | 7 | Minimum score to pass (tier-relax hota hai min-5 ke liye) |
| `MAX_PER_SECTOR` | 3 | Sector diversification cap |
| `HIST_DAYS` | 40 | Kitne trading sessions ki bhavcopy history |
| `SCAN_LIMIT` | 0 | 0 = full scan; testing ke liye chhota number |
| `FORCE_TODAY` | — | `DDMMYYYY` — past date pe paper-run (weekend test) |

Workflow me env values `.github/workflows/daily.yml` ke `env:` block me hain — wahin edit karo.

---

## STRATEGY — DONO DIRECTION (sab previous-day close pe)

**LONG (breakout family):** 20D high breakout (ya high ke 0.5% andar) + volume
expansion (1.3-1.8x of 20-day avg) + green + strong close (day-range ke 70% upar) +
uptrend (close > 20SMA, SMA rising) + RS vs Nifty (+3% over 20d) + delivery ≥ 40%

**SHORT (breakdown family):** 20D low breakdown (ya low ke 0.5% andar) + volume
expansion + red + weak close (day-range ke 30% neeche) + downtrend (close < 20SMA,
SMA falling) + RS negative (−3% over 20d) + delivery ≥ 40%. Cash intraday MIS short
= sell first, same day buy-back, 3:20 tak square off.

**Rejects (dono side):** over-extended (20D level se 5%+ door), dead stock (ATR < 0.6%),
SL bahut tight (< 0.4%), penny (< ₹50), low turnover (< ₹5cr/day)

**Levels:**
- LONG:  Entry = signal-day high +0.1% (trigger), SL = entry − 0.7×ATR(14), Target = entry + 2×risk
- SHORT: Entry = signal-day low −0.1% (trigger), SL = entry + 0.7×ATR(14), Target = entry − 2×risk
- Qty = min(RISK/risk-per-share, CAPITAL×5/entry)

**Scoring (/12, mirror dono side):** BREAKOUT/BREAKDOWN=3, HIGH/LOW KE PAAS=2,
VOL>1.8x=2, VOL>1.3x=1, GREEN/RED=1, STRONG/WEAK CLOSE=1, UPTREND+/DOWNTREND+=2,
basic trend=1, RS>NIFTY/RS<NIFTY=1, ATR sweet (1-5%)=1, DELIV≥40%=1

**Market bias:** WEAK market (Nifty-ETF proxy 20SMA ke neeche + SMA girta hua) me
aadhe slot SHORT setups ko milte hain (WEAK-MKT BIAS tag) + red banner dikhta hai.

---

## DATA PIPELINE (100% NSE/BSE — zero Yahoo)

1. **Universe:** NSE Nifty-500 official CSV → fail hone pe built-in sector-diverse fallback list
2. **History:** last ~40 trading sessions ki official bhavcopy — `sec_bhavdata_full`
   (delivery % ke saath). NSE block/down ho to **BSE UDiFF bhavcopy** fallback
   (delivery % nahi milta, baaki sab same). Parallel download, do NSE hosts try.
3. **FII/DII:** NSE `eq_fiidii_hist_csv.csv` → fail to NSE API (cookie warmup).
   Last 5 sessions ka FII/DII net (₹ Cr) page pe table + sentiment verdict.
   Flows sentiment ke liye hain — entry signal ke liye NAHI.
4. **Market regime:** NIFTYBEES (Nifty-50 ETF) ki bhavcopy history se — close vs
   20SMA. Yahoo ki zaroorat hi nahi padi.
5. **Scoring + min-5 guarantee:** dono direction score, best direction jeet,
   sector cap 3, tier-relax se kam se kam 5 picks.

**Note:** bhavcopy shaam ~7-8 PM tak publish hota hai. 8 PM IST run pe aaj ka data
mil jata hai; agar file late hui to pichhle session ka data use hota hai — page pe
signal date saaf likha rehta hai.

---

## KAL KE 5 EXECUTION RULES (page pe bhi print hote hain)

1. Entry sirf TRIGGER ke baad: **LONG = ENTRY ke UPAR** 5-min candle close;
   **SHORT = ENTRY ke NEECHE** 5-min candle close. Bina trigger trade NAHI.
2. **0.7%+ gap** ho to trade **SKIP** — gap-up pe long chase nahi, gap-down pe short chase nahi.
3. Entry ke saath hi **SL order** — SHORT ka SL entry ke UPAR hota hai. SL hit = turant bahar.
4. **Max 3 trades/day**; 2 winner = ₹1,000 target = **BAND**. 3:00 PM ke baad naya entry nahi.
5. Result/RBI/expiry day pe **size aadha**. FII/DII = sentiment, entry signal NAHI.

---

## GOTCHAS (pehle se pata hain, system ne handle kar rakha hai)

- Browser se NSE live data impossible hai (CORS + no public API) — isliye EOD approach
- NSE archives cloud IPs ko block karti hai → BSE UDiFF fallback + do NSE host try +
  dono fail to purane outputs safe (script exit, overwrite NAHI)
- Delivery % sirf NSE source se milta hai — BSE-fallback wale din wo 1 point skip hota hai
- GitHub cron 5-30 min late chal sakta hai — EOD system ke liye farak nahi padta
- Kabhi-kabhi 5 se kam hi qualify hoga (even weak-pass ke baad) — bazaar aaj hi aisa hai,
  force trade = loss. LOW CONF / WEAK PASS tags dekh ke size decide karo.
- Same day dobara run karo to history.csv me us plan-date ke rows replace hote hain (dupes nahi)
- **history.csv 2 hafte baad audit karna — asli teacher wahi hai.** SHORT rows alag se
  dekho: girta bazaar me bhi paisa banta hai.

---

*Educational research tool — financial advice nahi. Derivatives nahi, sirf cash/MIS.
Bina SL ke trade = gambling.*
