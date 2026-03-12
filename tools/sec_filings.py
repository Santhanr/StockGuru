"""SEC EDGAR Form 4 insider buying — no API key needed."""
import json
import requests
from langchain_core.tools import tool
import config


def _get_cik(ticker: str, headers: dict) -> str | None:
    """Look up CIK for a ticker using EDGAR's official company tickers JSON."""
    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    for entry in r.json().values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


@tool
def get_recent_form4_filings(ticker: str) -> str:
    """Fetch recent Form 4 insider buying filings from SEC EDGAR (free, no API key)."""
    headers = {"User-Agent": config.SEC_USER_AGENT}

    try:
        cik = _get_cik(ticker, headers)
        if not cik:
            return f"Could not find CIK for ticker {ticker} on SEC EDGAR."

        # Submissions API: returns structured recent filings sorted by date desc
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()

        company_name = data.get("name", ticker)
        recent = data.get("filings", {}).get("recent", {})

        forms       = recent.get("form", [])
        dates       = recent.get("filingDate", [])
        accessions  = recent.get("accessionNumber", [])
        reporters   = recent.get("reportingOwner", []) if "reportingOwner" in recent else [""] * len(forms)

        form4s = []
        for i, form in enumerate(forms):
            if form == "4":
                acc = accessions[i] if i < len(accessions) else ""
                acc_nodash = acc.replace("-", "")
                form4s.append({
                    "filing_date": dates[i] if i < len(dates) else None,
                    "accession_no": acc,
                    "sec_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/",
                })
            if len(form4s) >= 10:
                break

        if not form4s:
            return f"No Form 4 filings found for {ticker} ({company_name}) in recent history."

        return json.dumps({
            "ticker": ticker,
            "company": company_name,
            "cik": cik,
            "recent_form4_filings": form4s,
            "note": "Form 4 = insider transaction report. Visit sec_url for full details.",
        }, indent=2)

    except Exception as exc:
        return f"[get_recent_form4_filings error] {exc}"
