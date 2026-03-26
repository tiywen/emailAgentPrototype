from __future__ import annotations

import html as html_module
import os
import re
import tempfile
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from email_assistant.models import Message, UnifiedInput
from email_assistant.preprocessor import build_thread_text


def graph_base_url() -> str:
    """Microsoft Graph API root; override for national clouds (see README)."""
    root = (os.getenv("GRAPH_API_ROOT") or "https://graph.microsoft.com/v1.0").strip().rstrip("/")
    return root


def _auth_headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def strip_html_to_text(raw: str) -> str:
    """Best-effort HTML to plain text for prototype (no extra deps)."""
    if not raw:
        return ""
    # Unescape entities, remove script/style blocks, strip tags
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def email_html_to_plain(raw: str) -> str:
    """HTML saved from an email client → readable plain text with line breaks preserved."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*tr\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*div\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts: List[str] = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _extract_eml_body(msg: Any) -> str:
    plain_parts: List[str] = []
    html_parts: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                payload = part.get_content()
            except Exception:
                continue
            if not isinstance(payload, str):
                continue
            if ctype == "text/plain":
                plain_parts.append(payload.strip())
            elif ctype == "text/html":
                html_parts.append(payload.strip())
    else:
        ctype = (msg.get_content_type() or "").lower()
        try:
            payload = msg.get_content()
        except Exception:
            payload = ""
        if isinstance(payload, str):
            if ctype == "text/html":
                html_parts.append(payload.strip())
            else:
                plain_parts.append(payload.strip())
    if plain_parts:
        return "\n\n".join([p for p in plain_parts if p])
    if html_parts:
        return email_html_to_plain("\n".join(html_parts))
    return ""


def email_eml_to_plain(raw: bytes) -> str:
    """Parse .eml bytes into normalized thread text."""
    if not raw:
        return ""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    subject = _decode_mime_header(msg.get("Subject")) or "(no subject)"
    sender = _decode_mime_header(msg.get("From")) or "unknown"
    to_line = _decode_mime_header(msg.get("To"))
    cc_line = _decode_mime_header(msg.get("Cc"))
    recipients = ", ".join([p for p in [to_line, cc_line] if p]) or "unknown"
    date_line = _decode_mime_header(msg.get("Date")) or "unknown"
    body = _extract_eml_body(msg).strip() or "(empty body)"
    return (
        f"Thread: uploaded-eml\n"
        f"Subject: {subject}\n\n"
        f"Message #1\n"
        f"From: {sender}\n"
        f"To: {recipients}\n"
        f"Time: {date_line}\n"
        f"Body:\n{body}"
    )


def email_msg_to_plain(raw: bytes) -> str:
    """Parse .msg bytes into normalized thread text (requires extract-msg)."""
    if not raw:
        return ""
    try:
        import extract_msg  # type: ignore
    except ImportError as err:
        raise RuntimeError("解析 .msg 需要安装依赖：extract-msg") from err

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".msg") as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        msg = extract_msg.Message(tmp_path)
        subject = (msg.subject or "").strip() or "(no subject)"
        sender = (msg.sender or "").strip() or "unknown"
        to_line = (msg.to or "").strip()
        cc_line = (msg.cc or "").strip()
        recipients = ", ".join([p for p in [to_line, cc_line] if p]) or "unknown"
        date_line = str(msg.date or "").strip() or "unknown"
        body = (msg.body or "").strip()
        if not body:
            html_body = (msg.htmlBody or b"")
            if isinstance(html_body, bytes):
                body = email_html_to_plain(html_body.decode("utf-8", errors="replace"))
            else:
                body = email_html_to_plain(str(html_body))
        body = body or "(empty body)"
        return (
            f"Thread: uploaded-msg\n"
            f"Subject: {subject}\n\n"
            f"Message #1\n"
            f"From: {sender}\n"
            f"To: {recipients}\n"
            f"Time: {date_line}\n"
            f"Body:\n{body}"
        )
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def uploaded_email_file_to_plain(raw: bytes, filename: str) -> str:
    ext = os.path.splitext((filename or "").lower())[1]
    if ext in (".html", ".htm"):
        text = raw.decode("utf-8", errors="replace")
        return email_html_to_plain(text)
    if ext == ".eml":
        return email_eml_to_plain(raw)
    if ext == ".msg":
        return email_msg_to_plain(raw)
    raise RuntimeError(f"不支持的文件类型：{ext or '(unknown)'}")


def graph_body_plain(body: Dict[str, Any] | None) -> str:
    if not body:
        return ""
    content = (body.get("content") or "").strip()
    ctype = (body.get("contentType") or "text").lower()
    if ctype == "html":
        return strip_html_to_text(content)
    return content


def graph_datetime_to_local_text(value: str) -> str:
    """Graph datetime (UTC ISO8601) -> local timezone text, e.g. 2026-03-25 18:30:00."""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Fallback: keep original if format is unexpected
        return raw


def _format_address(addr: Optional[Dict[str, Any]]) -> str:
    if not addr:
        return "unknown"
    inner = addr.get("emailAddress") or addr
    if isinstance(inner, dict):
        name = (inner.get("name") or "").strip()
        email = (inner.get("address") or "").strip()
        if name and email:
            return f"{name} <{email}>"
        return email or name or "unknown"
    return str(inner)


def _recipient_list(recipients: Any) -> List[str]:
    if not recipients:
        return []
    out: List[str] = []
    for item in recipients:
        if isinstance(item, dict):
            out.append(_format_address(item))
    return [r for r in out if r]


def list_inbox_messages(
    access_token: str,
    *,
    top: int = 15,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """Fetch inbox messages with robust fallbacks across account types.

    Strategy:
    1) Prefer strict inbox endpoint: ``/me/mailFolders/inbox/messages``.
    2) Fallback: resolve inbox folder id and query ``/me/messages`` with folder filter.
    3) Last resort: ``/me/messages`` (not strictly inbox, but keeps app usable).
    """
    base = graph_base_url()
    common_attempts: List[Dict[str, str]] = [
        {
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments",
        },
        {
            "$top": str(top),
            "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments",
        },
        {"$top": str(top)},
    ]
    last_status = 0
    last_body = ""

    # 1) Strict inbox endpoint
    inbox_url = f"{base}/me/mailFolders/inbox/messages"
    for params in common_attempts:
        resp = requests.get(inbox_url, headers=_auth_headers(access_token), params=params, timeout=timeout)
        last_status, last_body = resp.status_code, resp.text or ""
        if resp.status_code == 200:
            data = resp.json()
            return list(data.get("value") or [])

    # 2) Folder id + filtered messages endpoint
    inbox_folder_url = f"{base}/me/mailFolders/inbox"
    folder_resp = requests.get(
        inbox_folder_url,
        headers=_auth_headers(access_token),
        params={"$select": "id"},
        timeout=timeout,
    )
    last_status, last_body = folder_resp.status_code, folder_resp.text or ""
    if folder_resp.status_code == 200:
        folder_id = (folder_resp.json().get("id") or "").strip()
        if folder_id:
            filtered_url = f"{base}/me/messages"
            filter_attempts: List[Dict[str, str]] = [
                {
                    "$top": str(top),
                    "$filter": f"parentFolderId eq '{folder_id}'",
                    "$orderby": "receivedDateTime desc",
                    "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments,parentFolderId",
                },
                {
                    "$top": str(top),
                    "$filter": f"parentFolderId eq '{folder_id}'",
                },
            ]
            for params in filter_attempts:
                resp = requests.get(filtered_url, headers=_auth_headers(access_token), params=params, timeout=timeout)
                last_status, last_body = resp.status_code, resp.text or ""
                if resp.status_code == 200:
                    data = resp.json()
                    return list(data.get("value") or [])

    # 3) Last resort fallback to generic /me/messages
    url = f"{base}/me/messages"
    for params in common_attempts:
        resp = requests.get(url, headers=_auth_headers(access_token), params=params, timeout=timeout)
        last_status, last_body = resp.status_code, resp.text or ""
        if resp.status_code == 200:
            data = resp.json()
            return list(data.get("value") or [])

    raise RuntimeError(
        f"Graph list messages failed ({last_status}) — "
        f"Tried inbox endpoint, inbox-folder filter, and /me/messages fallback. "
        f"Body: {last_body[:700]}"
    )


def graph_probe_me(access_token: str, *, timeout: int = 30) -> tuple[int, str]:
    """GET /me — minimal check that the token is accepted by Graph host."""
    url = f"{graph_base_url()}/me"
    resp = requests.get(
        url,
        headers=_auth_headers(access_token),
        params={"$select": "id,displayName,mail,userPrincipalName"},
        timeout=timeout,
    )
    return resp.status_code, (resp.text or "")[:800]


def graph_get_me(access_token: str, *, timeout: int = 30) -> Dict[str, Any]:
    """GET /me and return JSON. Use for UI identity display."""
    url = f"{graph_base_url()}/me"
    resp = requests.get(
        url,
        headers=_auth_headers(access_token),
        params={"$select": "id,displayName,mail,userPrincipalName"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Graph /me failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    if not isinstance(data, dict):
        return {}
    return data


def get_message_detail(access_token: str, message_id: str, *, timeout: int = 30) -> Dict[str, Any]:
    """Fetch one message with body and routing fields for analysis."""
    safe_id = quote(message_id, safe="")
    url = f"{graph_base_url()}/me/messages/{safe_id}"
    params = {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,bodyPreview",
    }
    resp = requests.get(url, headers=_auth_headers(access_token), params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Graph get message failed ({resp.status_code}): {resp.text}")
    return resp.json()


def graph_message_to_thread_text(detail: Dict[str, Any]) -> str:
    """Convert a single Graph message into the same text shape as build_thread_text."""
    subject = (detail.get("subject") or "").strip() or "(no subject)"
    sender = _format_address(detail.get("from"))
    to_list = _recipient_list(detail.get("toRecipients"))
    cc_list = _recipient_list(detail.get("ccRecipients"))
    recipients = to_list + ([f"cc: {c}" for c in cc_list] if cc_list else [])
    ts = graph_datetime_to_local_text((detail.get("receivedDateTime") or "").strip()) or "unknown"
    body_plain = graph_body_plain(detail.get("body")) or (detail.get("bodyPreview") or "").strip()

    msg = Message(
        sender=sender,
        recipients=recipients,
        timestamp=ts,
        body=body_plain or "(empty body)",
    )
    uid = (detail.get("id") or "").strip() or "graph-message"
    unified = UnifiedInput(
        input_type="single",
        thread_id=uid,
        subject=subject,
        messages=[msg],
    )
    return build_thread_text(unified)
