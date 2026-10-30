"""Real Gmail REST retrieval; never sends, labels, imports or deletes mail."""
import base64
from datetime import date, datetime, time, timedelta, timezone
from email.message import Message
from email.utils import parseaddr
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import time as clock
from urllib.parse import quote

import requests


def period(value):
    parts = str(value).split("/")
    if len(parts) != 2:
        raise ValueError("日期范围必须为 YYYY-MM-DD/YYYY-MM-DD（含首尾日期）")
    start, end = map(date.fromisoformat, parts)
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    return start, end


def fingerprint(comms_type, scope_name, reporting_period, planning_period, senders, keywords):
    return hashlib.sha256(json.dumps([comms_type, scope_name, reporting_period,
        planning_period if comms_type == "3P" else "", senders, keywords], ensure_ascii=False).encode()).hexdigest()


def query_for(comms_type, reporting_period, planning_period, senders, keywords):
    offset = os.getenv("GMAIL_TIMEZONE_OFFSET", "+08:00")
    if not re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", offset):
        raise ValueError("GMAIL_TIMEZONE_OFFSET 必须为 +08:00 等时区偏移")
    delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[4:]))
    zone = timezone(delta if offset[0] == "+" else -delta)
    ranges = [reporting_period] + ([planning_period] if comms_type == "3P" else [])
    windows = []
    for value in dict.fromkeys(ranges):
        start, end = period(value)
        # after is exclusive: subtract one second to include midnight.
        a = int(datetime.combine(start, time(), zone).timestamp()) - 1
        b = int(datetime.combine(end + timedelta(days=1), time(), zone).timestamp())
        windows.append(f"(after:{a} before:{b})")
    if not senders or not keywords:
        raise ValueError("必须先收集发件人和关键词")
    def literal(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ') + '"'
    addresses = [parseaddr(value)[1] if '@' in value else value for value in senders]
    return " ".join(["{" + " ".join(windows) + "}",
        "{" + " ".join("from:" + literal(value) for value in addresses) + "}",
        "{" + " ".join(literal(value) for value in keywords) + "}"])


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.skip, self.links = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.text.append("\n")
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag == "a" and self.links:
            link = self.links.pop()
            if link.startswith(("https://", "http://")):
                self.text.append(f" ({link})")

    def handle_data(self, value):
        if not self.skip:
            self.text.append(value)


class GmailClient:
    base = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, token_file=None, credentials_file=None, session=None):
        self.token_path = Path(token_file or os.getenv("GMAIL_TOKEN_FILE", "./config/token.json"))
        self.credentials_path = Path(credentials_file or os.getenv("GMAIL_CREDENTIALS_FILE", "./config/credentials.json"))
        self.token = json.loads(self.token_path.read_text(encoding="utf-8-sig"))
        self.session = session or requests.Session()

    def close(self):
        self.session.close()

    def refresh(self):
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8-sig"))
        client = credentials.get("installed", credentials.get("web", credentials))
        if not self.token.get("refresh_token"):
            raise RuntimeError("Gmail token 缺少 refresh_token，请更新 config/token.json")
        response = self.session.post("https://oauth2.googleapis.com/token", data={
            "client_id": self.token.get("client_id") or client["client_id"],
            "client_secret": self.token.get("client_secret") or client["client_secret"],
            "refresh_token": self.token["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Gmail 认证刷新失败（HTTP {response.status_code}），请重新授权并替换 token.json")
        result = response.json()
        self.token["token"] = result["access_token"]
        if result.get("refresh_token"):
            self.token["refresh_token"] = result["refresh_token"]
        self.token["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 3600))).isoformat()
        temporary = self.token_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.token, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.token_path)

    def get(self, path, params=None):
        if not self.token.get("token"):
            self.refresh()
        refreshed = False
        for attempt in range(4):
            response = self.session.get(self.base + path, params=params,
                headers={"Authorization": "Bearer " + self.token["token"]}, timeout=45)
            if response.status_code == 401 and not refreshed:
                self.refresh(); refreshed = True
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                clock.sleep(2 ** attempt)
                continue
            if response.status_code != 200:
                try:
                    reason = response.json().get("error", {}).get("message", "")
                except ValueError:
                    reason = ""
                raise RuntimeError(f"Gmail 检索失败（HTTP {response.status_code}）：{reason[:300]}")
            return response.json()
        raise RuntimeError("Gmail 检索重试后仍失败")

    def body(self, message_id, payload):
        plain, html = [], []
        def walk(part):
            mime = part.get("mimeType", "")
            if not part.get("filename") and mime in ("text/plain", "text/html"):
                body = part.get("body", {})
                if body.get("attachmentId"):
                    body = self.get(f"/messages/{quote(message_id, safe='')}/attachments/{quote(body['attachmentId'], safe='')}")
                raw = body.get("data", "")
                headers = {h['name'].lower(): h['value'] for h in part.get('headers', [])}
                header = Message(); header['content-type'] = headers.get('content-type', mime)
                data = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
                try:
                    value = data.decode(header.get_content_charset() or "utf-8", errors="replace")
                except LookupError:
                    value = data.decode('utf-8', errors='replace')
                if mime == "text/plain": plain.append(value)
                else:
                    parser = HTMLText(); parser.feed(value); html.append(''.join(parser.text))
            for child in part.get("parts", []): walk(child)
        walk(payload)
        return '\n'.join(plain or html).strip()

    def retrieve(self, query, stamp):
        limit = int(os.getenv("GMAIL_MAX_MESSAGES", "200"))
        if not 1 <= limit <= 2000:
            raise ValueError("GMAIL_MAX_MESSAGES 须在 1–2000 之间")
        ids, page = {}, None
        while True:
            params = {"q": query, "maxResults": min(500, limit + 1), "includeSpamTrash": "false"}
            if page: params["pageToken"] = page
            result = self.get('/messages', params)
            ids.update({item['id']: item for item in result.get('messages', [])})
            if len(ids) > limit or (len(ids) == limit and result.get('nextPageToken')):
                raise RuntimeError(f"检索超过 {limit} 封邮件，请缩小范围或调整 GMAIL_MAX_MESSAGES；未截断生成草稿")
            page = result.get('nextPageToken')
            if not page: break
        evidence = []
        for id in ids:
            message = self.get('/messages/' + quote(id, safe=''), {'format': 'full'})
            payload = message.get('payload', {})
            headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
            body = self.body(id, payload)
            if not body:
                raise RuntimeError(f"邮件 {id} 未能读取正文；不会用摘要冒充完整正文")
            evidence.append({'id': id, 'thread_id': message.get('threadId', id),
                'from': headers.get('from', ''), 'date': headers.get('date', ''),
                'internal_date_ms': message.get('internalDate', ''), 'subject': headers.get('subject', ''),
                'body': body, 'url': 'https://mail.google.com/mail/u/0/#all/' + message.get('threadId', id),
                'query': query, 'retrieval_fingerprint': stamp})
        return sorted(evidence, key=lambda item: (item['internal_date_ms'], item['id']))
