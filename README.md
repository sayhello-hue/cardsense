# CardSense India

A local-first Streamlit MVP for analysing Indian credit/debit-card statements and estimating which cards to keep, review, or add.

## What it does
- Upload multiple PDF, CSV, XLSX/XLS statements.
- Map each statement to a card/account.
- Extract and normalize transactions.
- Auto-categorize common merchants such as Swiggy, Blinkit, Amazon, Myntra, IndianOil, Expedia, utilities and UPI.
- Let you manually correct categories before recommendations.
- Show spend by category and month.
- Estimate current vs optimized reward value.
- Flag cards with weak observed economics for renewal review.
- Estimate incremental value of candidate cards.
- Export a normalized CSV for deeper review in ChatGPT.

## Run locally
1. Install Python 3.10+.
2. Open Terminal in this folder.
3. Create a virtual environment if desired.
4. Install dependencies:

   pip install -r requirements.txt

5. Run:

   streamlit run app.py

Streamlit will open the app in your browser, usually at http://localhost:8501.

## Privacy
For maximum privacy, run this application locally. Statement files are processed in-memory by the Streamlit process. The app does not intentionally send statement contents to external analytics or APIs.

## Important
PDF statements vary greatly by issuer, so PDF extraction is heuristic. CSV/XLSX bank exports are preferred. Always inspect and correct the parsed transaction table before acting on recommendations.

Reward terms change frequently. The rules live in `card_catalog.json` and are intentionally editable. The seeded catalog was last verified on 2026-09-23 for key rules from HDFC Bank, RBL Bank, HSBC and SBI Card; other entries may need exact-variant verification before acting.
