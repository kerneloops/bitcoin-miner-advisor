import json
import logging
import os
from datetime import date

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",  # min-privilege: only files this app creates
]

SHEET_HEADERS = [
    "Date", "Ticker", "Price", "RSI", "1W%", "1M%",
    "SMA20", "SMA50", "BTC Corr", "Recommendation",
    "Confidence", "Reasoning", "Key Risk", "BTC Trend",
]


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") and os.getenv("GOOGLE_SHEET_ID"))


def _get_missing() -> list[str]:
    missing = []
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not os.getenv("GOOGLE_SHEET_ID"):
        missing.append("GOOGLE_SHEET_ID")
    return missing


def _build_credentials():
    return service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )


def _ticker_to_row(run_date: str, d: dict) -> list:
    return [
        run_date,
        d.get("ticker", ""),
        d.get("current_price"),
        d.get("rsi"),
        d.get("week_return_pct"),
        d.get("month_return_pct"),
        d.get("sma20"),
        d.get("sma50"),
        d.get("btc_correlation"),
        d.get("recommendation", ""),
        d.get("confidence", ""),
        d.get("reasoning", ""),
        d.get("key_risk", ""),
        d.get("btc_trend", ""),
    ]


def append_to_sheet(analysis_data: dict) -> str:
    """Append one row per ticker to the Google Sheet. Returns spreadsheet URL."""
    creds = _build_credentials()
    service = build("sheets", "v4", credentials=creds)
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    spreadsheet = service.spreadsheets()

    existing = spreadsheet.values().get(
        spreadsheetId=sheet_id, range="Sheet1!A1:A1"
    ).execute()

    rows = []
    if not existing.get("values"):
        rows.append(SHEET_HEADERS)

    run_date = date.today().isoformat()
    for d in analysis_data.get("tickers", {}).values():
        if "error" not in d:
            rows.append(_ticker_to_row(run_date, d))

    if rows:
        spreadsheet.values().append(
            spreadsheetId=sheet_id,
            range="Sheet1!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def upload_to_drive(analysis_data: dict) -> str:
    """Upload JSON report to Google Drive. Returns file webViewLink."""
    creds = _build_credentials()
    service = build("drive", "v3", credentials=creds)

    filename = f"btc-miner-analysis-{date.today().isoformat()}.json"
    content = json.dumps(analysis_data, indent=2).encode("utf-8")

    metadata = {"name": filename, "mimeType": "application/json"}
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaInMemoryUpload(content, mimetype="application/json", resumable=False)
    file = service.files().create(
        body=metadata, media_body=media, fields="id,webViewLink"
    ).execute()

    return file["webViewLink"]
