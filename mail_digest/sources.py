import base64
import email
import imaplib
import json
import ssl
import time
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .secrets import get_secret, put_secret


GMAIL_SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean_html(value):
    parser = _Text()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def decode_mime(value):
    try:
        return str(make_header(decode_header(value or "")))
    except (UnicodeError, email.errors.HeaderParseError):
        return value or ""


def extract_body(message):
    candidates = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        raw = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        value = raw.decode(charset, errors="replace")
        if part.get_content_type() == "text/html":
            value = clean_html(value)
        candidates.append((part.get_content_type() == "text/plain", value))
    candidates.sort(reverse=True)
    return " ".join(candidates[0][1].split()) if candidates else ""


def normalize(account, message_id, message, excerpt_chars):
    try:
        when = parsedate_to_datetime(message.get("Date", ""))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        when = datetime.now(timezone.utc)
    return {
        "id": message_id,
        "account": account,
        "sender": decode_mime(message.get("From", ""))[:300],
        "subject": decode_mime(message.get("Subject", ""))[:500],
        "excerpt": extract_body(message)[:excerpt_chars],
        "received": when.astimezone(timezone.utc).isoformat(),
    }


def fetch_imap(account, cfg, since, excerpt_chars):
    for attempt in range(3):
        try:
            return _fetch_imap_once(account, cfg, since, excerpt_chars)
        except (ssl.SSLError, imaplib.IMAP4.abort, ConnectionError, TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep((2, 5)[attempt])


def _fetch_imap_once(account, cfg, since, excerpt_chars):
    address = cfg["address"]
    conn = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port", 993)), timeout=30)
    try:
        conn.login(address, get_secret(f"{account}_password"))
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("无法只读打开收件箱")
        status, values = conn.response("UIDVALIDITY")
        validity = (values or [b"unknown"])[0]
        if isinstance(validity, bytes):
            validity = validity.decode("ascii", errors="replace")
        status, values = conn.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
        if status != "OK":
            raise RuntimeError("IMAP 搜索失败")
        result = []
        for uid in (values[0] or b"").split():
            status, data = conn.uid("fetch", uid, "(BODY.PEEK[] INTERNALDATE)")
            if status != "OK":
                raise RuntimeError("IMAP 获取邮件失败")
            part = next((item for item in data if isinstance(item, tuple)), None)
            if part is None:
                continue
            message = email.message_from_bytes(part[1])
            normalized = normalize(account, f"{validity}:{uid.decode()}", message, excerpt_chars)
            internal = imaplib.Internaldate2tuple(part[0])
            if internal:
                normalized["received"] = datetime.fromtimestamp(time.mktime(internal), timezone.utc).isoformat()
            if datetime.fromisoformat(normalized["received"]) >= since:
                result.append(normalized)
        return result
    finally:
        try:
            conn.logout()
        except imaplib.IMAP4.error:
            pass


def gmail_credentials(interactive=False):
    try:
        creds = Credentials.from_authorized_user_info(json.loads(get_secret("gmail_token")), GMAIL_SCOPE)
    except (RuntimeError, ValueError, KeyError):
        creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            put_secret("gmail_token", creds.to_json())
        except RefreshError:
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("Gmail 授权缺失或过期；请运行 gmail-auth")
        client = json.loads(get_secret("gmail_client"))
        flow = InstalledAppFlow.from_client_config(client, GMAIL_SCOPE)
        creds = flow.run_local_server(port=0)
        put_secret("gmail_token", creds.to_json())
    return creds


def fetch_gmail(account, since, excerpt_chars, expected_address=None):
    service = build("gmail", "v1", credentials=gmail_credentials(), cache_discovery=False)
    if expected_address:
        actual = service.users().getProfile(userId="me").execute()["emailAddress"]
        if actual.casefold() != expected_address.casefold():
            raise RuntimeError("Gmail 授权账号与配置的邮箱地址不一致")
    query = f"in:inbox after:{int(since.timestamp())}"
    result = []
    page = None
    while True:
        response = service.users().messages().list(userId="me", q=query, pageToken=page, maxResults=100).execute()
        for item in response.get("messages", []):
            data = service.users().messages().get(userId="me", id=item["id"], format="raw").execute()
            raw = base64.urlsafe_b64decode(data["raw"] + "===")
            normalized = normalize(account, item["id"], email.message_from_bytes(raw), excerpt_chars)
            if data.get("internalDate"):
                normalized["received"] = datetime.fromtimestamp(int(data["internalDate"]) / 1000, timezone.utc).isoformat()
            if datetime.fromisoformat(normalized["received"]) >= since:
                result.append(normalized)
        page = response.get("nextPageToken")
        if not page:
            return result
