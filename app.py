import io, json, re
from pathlib import Path
import pandas as pd
import streamlit as st
from pypdf import PdfReader

APP_DIR = Path(__file__).resolve().parent
CATALOG = json.loads((APP_DIR / "card_catalog.json").read_text())
CARDS = {c["name"]: c for c in CATALOG["cards"]}

st.set_page_config(page_title="CardSense India", page_icon="💳", layout="wide")

# --- access gate (set APP_PASSWORD in Streamlit Cloud -> Settings -> Secrets) ---
def _gate():
    try:
        expected = st.secrets["APP_PASSWORD"]
    except Exception:
        return True  # no secret set (e.g. running locally) -> open
    if st.session_state.get("_ok"):
        return True
    st.title("CardSense India")
    code = st.text_input("Access code", type="password")
    if code:
        if code == expected:
            st.session_state["_ok"] = True
            st.rerun()
        else:
            st.error("Wrong code")
    return False

if not _gate():
    st.stop()
# --- end gate ---

st.title("💳 CardSense India")
st.caption("Upload this year's credit/debit card statements → understand spend → decide what to keep, close, or apply for.")

with st.sidebar:
    st.header("Privacy")
    st.success("Statements are parsed in memory only. Nothing is written to disk, stored, or sent anywhere. Close the tab and it is gone.")
    st.caption("For maximum privacy, run this app locally on your own computer.")
    st.header("Assumptions")
    is_prime = st.checkbox("Amazon Prime member", value=True)
    st.header("PDF passwords")
    st.caption("Different issuers use different statement passwords. Put every one you use here, one per line \u2014 each file is tried against all of them until one opens it.")
    _pw_blob = st.text_area(
        "Passwords (one per line)",
        value="",
        height=120,
        placeholder="SHUB1503\nyesbank@123\n4509",
        help="Tried in order against every encrypted PDF you upload. Never stored or sent anywhere.",
    )
    PDF_PASSWORDS = [ln.strip() for ln in _pw_blob.splitlines() if ln.strip()]
    if PDF_PASSWORDS:
        st.caption(f"{len(PDF_PASSWORDS)} password(s) loaded for this session.")
    foreign_markup_baseline = st.number_input("Typical forex markup on alternative card (%)", 0.0, 5.0, 3.5, 0.1)

DEFAULT_EXISTING = [
    "YES Bank World Select", "HDFC Marriott Bonvoy", "HDFC Regalia Gold",
    "Uni GoldX", "RBL World Safari", "RBL IndianOil", "HDFC Swiggy Ornge",
    "YES BANK RuPay Credit Card"
]
existing = st.multiselect("Cards you currently hold", list(CARDS), default=DEFAULT_EXISTING)

st.markdown("### 1) Upload statements")
files = st.file_uploader("Credit-card bills, debit-card exports or transaction files", type=["pdf","csv","xlsx","xls"], accept_multiple_files=True)
st.caption("Best results: bank CSV/XLSX exports. PDFs are supported with heuristic parsing and should be reviewed before using recommendations.")

DATE_PAT = re.compile(r"(?P<date>\b\d{1,2}[/-](?:\d{1,2}|[A-Za-z]{3})[/-](?:\d{2,4})\b)")
AMT_PAT = re.compile(r"(?P<amount>[\d,]+\.\d{2})(?:\s*(?P<drcr>CR|DR))?\s*$", re.I)

def normalize_amount(x):
    if pd.isna(x): return None
    s = str(x).replace("₹", "").replace(",", "").strip()
    s = re.sub(r"[^0-9.\-]", "", s)
    try: return float(s)
    except: return None

def parse_pdf(uploaded, passwords=()):
    """Parse a statement PDF, trying every supplied password until one opens it.

    Returns (DataFrame, password_that_worked | None).
    """
    data = uploaded.getvalue()
    reader = PdfReader(io.BytesIO(data))
    used_password = None

    if reader.is_encrypted:
        # blank first (some PDFs are encrypted with an empty owner password)
        candidates, seen = [], set()
        for pw in [""] + [p for p in passwords if p]:
            if pw not in seen:
                seen.add(pw)
                candidates.append(pw)

        opened = False
        for pw in candidates:
            try:
                trial = PdfReader(io.BytesIO(data))   # fresh reader per attempt
                if trial.decrypt(pw):
                    reader, used_password, opened = trial, (pw or None), True
                    break
            except Exception:
                continue

        if not opened:
            n = len([c for c in candidates if c])
            if n == 0:
                raise ValueError("Encrypted PDF \u2014 add its password in the sidebar")
            raise ValueError(f"Encrypted PDF \u2014 tried {n} password(s), none opened it")
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    rows = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        dm = DATE_PAT.search(line)
        am = AMT_PAT.search(line)
        if not (dm and am):
            continue
        amount = normalize_amount(am.group("amount"))
        if amount is None: continue
        if (am.group("drcr") or "").upper() == "CR": amount = -amount
        desc = line[dm.end():am.start()].strip(" -|:")
        if len(desc) < 2: continue
        rows.append({"date": dm.group("date"), "description": desc, "amount": amount})
    return pd.DataFrame(rows), used_password

def parse_tabular(uploaded):
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(uploaded.getvalue()))
    else:
        raw = pd.read_excel(io.BytesIO(uploaded.getvalue()))
    cols = {c.lower().strip(): c for c in raw.columns}
    date_col = next((cols[k] for k in cols if any(t in k for t in ["date", "txn"])), None)
    desc_col = next((cols[k] for k in cols if any(t in k for t in ["description", "narration", "merchant", "details", "particular"])), None)
    amt_col = next((cols[k] for k in cols if k in ["amount", "transaction amount", "txn amount", "debit"] or "amount" in k), None)
    credit_col = next((cols[k] for k in cols if "credit" in k), None)
    debit_col = next((cols[k] for k in cols if "debit" in k), None)
    if date_col is None or desc_col is None:
        raise ValueError("Couldn't identify date/description columns")
    out = pd.DataFrame({"date": raw[date_col], "description": raw[desc_col].astype(str)})
    if debit_col and credit_col:
        out["amount"] = raw[debit_col].map(normalize_amount).fillna(0) - raw[credit_col].map(normalize_amount).fillna(0)
    elif amt_col:
        out["amount"] = raw[amt_col].map(normalize_amount)
    else:
        raise ValueError("Couldn't identify amount/debit/credit column")
    return out

def categorize(desc):
    d = str(desc).lower()
    rules = [
        ("swiggy", ["swiggy"]), ("blinkit", ["blinkit", "grofers"]),
        ("amazon", ["amazon", "amzn"]), ("myntra", ["myntra"]),
        ("indianoil", ["indian oil", "iocl", "indianoil"]),
        ("fuel", ["petrol", "fuel", "hpcl", "bharat petroleum", "bpcl", "shell"]),
        ("marriott", ["marriott", "westin", "sheraton", "courtyard", "fairfield"]),
        ("travel", ["expedia", "makemytrip", "goibibo", "air india", "indigo", "vistara", "akasa", "booking.com", "agoda", "airbnb", "hotel", "airlines"]),
        ("utilities", ["airtel", "jio", "vodafone", "vi ", "bsnl", "broadband", "wifi", "electricity", "bescom", "discom", "recharge"]),
        ("grocery", ["bigbasket", "zepto", "dmart", "reliance fresh", "grocery", "supermarket"]),
        ("food_delivery", ["zomato", "food delivery"]),
        ("dining", ["restaurant", "cafe", "coffee", "bar ", "dining"]),
        ("upi", ["upi", "bharatpe", "phonepe", "gpay", "google pay"]),
        ("online_shopping", ["flipkart", "ajio", "tatacliq", "meesho", "shopping"]),
        ("international", ["intl", "international", "usd ", "eur ", "gbp ", "aed "])
    ]
    for cat, kws in rules:
        if any(k in d for k in kws): return cat
    return "other"

def reward_for(card, row):
    cat = row["category"]
    amount = max(float(row["amount"]), 0)
    rates = card.get("rates", {})
    rate = rates.get(cat, card.get("base_rate", 0))
    if card["name"] == "Amazon Pay ICICI" and cat == "amazon" and not is_prime:
        rate = 0.03
    # YES BANK RuPay Credit Card: accelerated UPI earn applies only when that individual
    # merchant UPI transaction is above ₹2,000. We conservatively model smaller
    # UPI transactions as earning no UPI reward.
    if card["name"] == "YES BANK RuPay Credit Card" and cat == "upi":
        rate = 0.005 if amount > 2000 else 0.0
    reward = amount * rate
    if cat == "international" and card["name"] == "Uni GoldX":
        reward += amount * foreign_markup_baseline / 100.0
    return reward

frames, file_errors = [], []
unlocked_with = {}
if files:
    st.markdown("#### Map each statement to the card/account used")
    for i, f in enumerate(files):
        c1, c2 = st.columns([2,2])
        with c1: st.write(f.name)
        with c2:
            account = st.selectbox("Account/card", ["Debit / Bank account"] + existing, key=f"acct_{i}")
        try:
            if f.name.lower().endswith(".pdf"):
                df, _used_pw = parse_pdf(f, PDF_PASSWORDS)
                if _used_pw:
                    unlocked_with[f.name] = _used_pw
            else:
                df = parse_tabular(f)
            if not df.empty:
                df["source_file"] = f.name
                df["account"] = account
                frames.append(df)
            else:
                file_errors.append(f"{f.name}: no transactions detected")
        except Exception as e:
            file_errors.append(f"{f.name}: {e}")

if unlocked_with:
    with st.expander(f"Unlocked {len(unlocked_with)} encrypted file(s) \u2014 which password worked"):
        for fname, pw in unlocked_with.items():
            masked = pw[:2] + "\u2022" * max(len(pw) - 2, 1)
            st.write(f"\u2022 **{fname}** \u2014 opened with `{masked}`")

if file_errors:
    with st.expander("Files needing attention"):
        for e in file_errors: st.warning(e)

if not frames:
    st.info("Upload statements to unlock the dashboard. You can also download the sample CSV from the project folder and test the flow.")
    st.stop()

tx = pd.concat(frames, ignore_index=True)
tx["date"] = pd.to_datetime(tx["date"], errors="coerce", dayfirst=True)
tx["amount"] = pd.to_numeric(tx["amount"], errors="coerce")
tx = tx.dropna(subset=["date","amount"])
tx = tx[tx["amount"] > 0].copy()  # spend only; credits/payments/refunds excluded from optimization

tx["category"] = tx["description"].map(categorize)
tx["month"] = tx["date"].dt.to_period("M").astype(str)

st.markdown("### 2) Review parsed transactions")
editable = st.data_editor(tx[["date","description","amount","category","account","source_file"]], width='stretch', num_rows="dynamic",
    column_config={"category": st.column_config.SelectboxColumn("category", options=["swiggy","blinkit","amazon","myntra","indianoil","fuel","marriott","travel","utilities","grocery","food_delivery","dining","upi","online_shopping","international","other"])})
tx = editable.copy()
tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
tx["amount"] = pd.to_numeric(tx["amount"], errors="coerce")
tx = tx.dropna(subset=["date","amount"])
tx["month"] = tx["date"].dt.to_period("M").astype(str)

m1,m2,m3,m4 = st.columns(4)
m1.metric("Spend analyzed", f"₹{tx.amount.sum():,.0f}")
m2.metric("Transactions", f"{len(tx):,}")
m3.metric("Months covered", tx["month"].nunique())
m4.metric("Cards/accounts", tx["account"].nunique())

c1,c2 = st.columns(2)
with c1:
    st.markdown("#### Spend by category")
    st.bar_chart(tx.groupby("category")["amount"].sum().sort_values(ascending=False))
with c2:
    st.markdown("#### Spend by month")
    st.line_chart(tx.groupby("month")["amount"].sum())

st.markdown("### 3) Optimization")
# Candidate pool includes existing + likely additions
candidate_pool = st.multiselect("Cards to compare", list(CARDS), default=list(dict.fromkeys(existing + ["HSBC Live+", "Amazon Pay ICICI", "CASHBACK SBI Card"])))

# Transaction-level best card, then monthly cap application approximation
rows = []
for idx, r in tx.iterrows():
    options = []
    for name in candidate_pool:
        card = CARDS[name]
        options.append((name, reward_for(card, r)))
    best = max(options, key=lambda x: x[1]) if options else (None,0)
    actual = r["account"] if r["account"] in CARDS else None
    actual_reward = reward_for(CARDS[actual], r) if actual else 0
    rows.append({**r.to_dict(), "current_reward_est": actual_reward, "best_card": best[0], "best_reward_est": best[1], "incremental_value": best[1]-actual_reward})
opt = pd.DataFrame(rows)

# Conservative cap adjustment: cap rewards at obvious monthly cashback caps.
def apply_caps(df):
    df = df.copy()
    df["best_reward_capped"] = df["best_reward_est"]
    for (month, cardname), ix in df.groupby(["month","best_card"]).groups.items():
        card = CARDS.get(cardname, {})
        caps = card.get("monthly_caps", {})
        if not caps: continue
        ids = list(ix)
        if "accelerated_total" in caps:
            accelerated = [i for i in ids if df.loc[i,"category"] in ["dining","food_delivery","grocery","shopping","utilities","swiggy","blinkit","myntra","amazon"]]
            total = df.loc[accelerated,"best_reward_capped"].sum() if accelerated else 0
            cap = caps["accelerated_total"]
            if total > cap and total > 0:
                df.loc[accelerated,"best_reward_capped"] *= cap/total
        if cardname == "RBL IndianOil" and "fuel" in caps:
            fuel_ids = [i for i in ids if df.loc[i,"category"] in ["fuel","indianoil"]]
            total = df.loc[fuel_ids,"best_reward_capped"].sum() if fuel_ids else 0
            cap = caps["fuel"]
            if total > cap and total > 0: df.loc[fuel_ids,"best_reward_capped"] *= cap/total
        if cardname == "CASHBACK SBI Card" and "online" in caps:
            online_ids = [i for i in ids if df.loc[i,"category"] in ["online_shopping","amazon","myntra"]]
            total = df.loc[online_ids,"best_reward_capped"].sum() if online_ids else 0
            cap = caps["online"]
            if total > cap and total > 0: df.loc[online_ids,"best_reward_capped"] *= cap/total
        if cardname == "YES BANK RuPay Credit Card":
            upi_ids = [i for i in ids if df.loc[i,"category"] == "upi" and float(df.loc[i,"amount"]) > 2000]
            # 5,000 YES Rewardz/month × up to ₹0.25/point = ₹1,250 modeled value.
            upi_cap_value = caps.get("upi_reward_points", 0) * 0.25
            total = df.loc[upi_ids,"best_reward_capped"].sum() if upi_ids else 0
            if upi_cap_value and total > upi_cap_value and total > 0:
                df.loc[upi_ids,"best_reward_capped"] *= upi_cap_value/total
            utility_ids = [i for i in ids if df.loc[i,"category"] == "utilities"]
            total_u = df.loc[utility_ids,"best_reward_capped"].sum() if utility_ids else 0
            utility_cap = caps.get("utilities_value", 0)
            if utility_cap and total_u > utility_cap and total_u > 0:
                df.loc[utility_ids,"best_reward_capped"] *= utility_cap/total_u
    return df
opt = apply_caps(opt)

annualized_factor = 12 / max(tx["month"].nunique(), 1)
current_est = opt["current_reward_est"].sum() * annualized_factor
best_est = opt["best_reward_capped"].sum() * annualized_factor
st.write(f"**Estimated annualized current-card value:** ₹{current_est:,.0f}")
st.write(f"**Estimated annualized value if each transaction used the best compared card:** ₹{best_est:,.0f}")
st.write(f"**Potential uplift before incremental annual fees:** ₹{max(best_est-current_est,0):,.0f}/year")

st.markdown("#### Best card by category based on your actual spend mix")
cat_rows = []
for cat, g in tx.groupby("category"):
    spend = g.amount.sum()
    vals=[]
    for name in candidate_pool:
        vals.append((name, sum(reward_for(CARDS[name], r) for _,r in g.iterrows())))
    best=max(vals,key=lambda x:x[1]) if vals else (None,0)
    cat_rows.append({"Category":cat,"Spend":spend,"Best card":best[0],"Estimated value":best[1]})
st.dataframe(pd.DataFrame(cat_rows).sort_values("Spend",ascending=False), width='stretch', hide_index=True)

st.markdown("#### Keep / review signals for cards you already own")
keep_rows=[]
for name in existing:
    card=CARDS[name]
    spend=tx.loc[tx.account==name,"amount"].sum()
    actual_val=opt.loc[opt.account==name,"current_reward_est"].sum()
    annual_spend=spend*annualized_factor
    fee=0 if (card.get("fee_waiver_spend") and annual_spend>=card["fee_waiver_spend"]) else card["annual_fee"]*(1+card.get("gst_on_fee",0))
    annual_val=actual_val*annualized_factor
    net=annual_val-fee
    unique_best = int((opt.best_card==name).sum())
    if fee==0 or net>1500 or unique_best>0:
        signal="KEEP / USE SELECTIVELY"
    elif annual_spend==0:
        signal="REVIEW: NO OBSERVED USE"
    else:
        signal="REVIEW AT RENEWAL"
    keep_rows.append({"Card":name,"Observed spend":spend,"Annualized spend":annual_spend,"Est. annual rewards":annual_val,"Est. fee incl GST":fee,"Net before non-cash perks":net,"Signal":signal})
st.dataframe(pd.DataFrame(keep_rows).sort_values("Net before non-cash perks",ascending=False), width='stretch', hide_index=True)

st.markdown("#### New-card opportunity")
base_existing = [x for x in existing if x in CARDS]
new_rows=[]
for name in [c for c in candidate_pool if c not in existing]:
    with_card = base_existing + [name]
    base_val=0; new_val=0
    for _,r in tx.iterrows():
        base_val += max([reward_for(CARDS[c],r) for c in base_existing] or [0])
        new_val += max([reward_for(CARDS[c],r) for c in with_card] or [0])
    inc=(new_val-base_val)*annualized_factor
    c=CARDS[name]
    fee=c["annual_fee"]*(1+c.get("gst_on_fee",0))
    # waive if all incremental spend were enough is too optimistic, so don't auto-waive candidate fees
    new_rows.append({"Candidate":name,"Gross incremental value/year":inc,"Annual fee incl GST":fee,"Net incremental value/year":inc-fee,"Interpretation":"APPLY CANDIDATE" if inc-fee>1500 else "LOW PRIORITY"})
st.dataframe(pd.DataFrame(new_rows).sort_values("Net incremental value/year",ascending=False), width='stretch', hide_index=True)

st.markdown("### 4) Export for a deeper ChatGPT review")
summary = opt[["date","description","amount","category","account","best_card","incremental_value"]].copy()
st.download_button("Download normalized transactions + optimizer output", summary.to_csv(index=False).encode(), "cardsense_analysis.csv", "text/csv")

with st.expander("Important limitations"):
    st.write("• PDF parsing is heuristic; always review the table.\n• Merchant category codes, exclusions, reward caps and limited-time offers can change.\n• Marriott/airline point values are subjective and redemption-dependent.\n• Non-cash perks (lounge access, hotel status, free-night awards, insurance) are not fully valued automatically.\n• A 'review' signal is not a command to close a card—credit age, utilization and issuer relationship can matter.\n• Reward rules are stored in card_catalog.json so they can be updated as issuers change terms.")

st.caption(f"Reward catalog last verified: {CATALOG['last_verified']}")
