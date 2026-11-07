"""Narrow validation and deterministic safety boundaries for extracted memory."""
from __future__ import annotations
import hashlib
import re

KINDS = {"fact", "decision", "context", "preference", "profile"}
GROUP_SCOPES = {"group", "member"}
STATES = {"active", "review", "superseded", "deleted"}

# An intentionally conservative denylist; it does not replace consent or data governance.
HIGH_RISK = re.compile(
    r"(密码|验证码|银行卡|身份证号|身份证号码|护照号|私人住址|家庭住址|忽略之前|无视系统|绕过限制|api.?key|secret.?key|bearer\s+|"
    r"\b(?:\d[ -]?){16,19}\b|\b1[3-9]\d{9}\b)", re.I
)


def safe_content(content: object, limit: int = 500) -> str:
    if not isinstance(content, str):
        raise ValueError("Memory content must be text")
    content = " ".join(content.split())
    if not 3 <= len(content) <= limit:
        raise ValueError("Memory content length out of range")
    if HIGH_RISK.search(content):
        raise ValueError("Potentially sensitive memory is not accepted")
    return content


def topic_key(value: object, content: str) -> str:
    if isinstance(value, str):
        value = " ".join(value.strip().lower().split())
        if 2 <= len(value) <= 90 and not any(ch in value for ch in "\r\n<>\\"):
            return value
    return hashlib.sha256(content.lower().encode("utf-8")).hexdigest()[:24]


def fingerprint(kind: str, content: str, subject_id: str) -> str:
    text = re.sub(r"\s+", "", content.lower())
    return hashlib.sha256((kind + "\x1f" + subject_id + "\x1f" + text).encode("utf-8")).hexdigest()


def tokens_for_search(query: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9][a-zA-Z0-9_-]{1,}", query.lower())
    seen = set()
    output = []
    for word in words[:25]:
        pieces = [word] if len(word) <= 3 or not re.search(r"[\u4e00-\u9fff]", word) else [word] + [word[i:i+2] for i in range(len(word) - 1)]
        for piece in pieces:
            if piece not in seen:
                output.append(piece)
                seen.add(piece)
    return output[:50]
