"""Trusted event identity and bounded plain-text extraction."""
from __future__ import annotations

import re
from dataclasses import dataclass

ROOM_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,256}@chatroom$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,256}$")


@dataclass(frozen=True)
class Scope:
    platform: str
    owner: str
    room: str


def valid_room(room: object) -> bool:
    return isinstance(room, str) and bool(ROOM_PATTERN.fullmatch(room))


def get_scope(event, platform_id: str, allowed_rooms: set[str]) -> Scope | None:
    if event.get_platform_name() != "wechat_pc_hook" or event.get_platform_id() != platform_id:
        return None
    room = event.get_group_id()
    msg = event.message_obj
    owner = getattr(msg, "self_id", None)
    if not valid_room(room) or room not in allowed_rooms:
        return None
    if not isinstance(owner, str) or not ID_PATTERN.fullmatch(owner):
        return None
    return Scope(platform_id, owner, room)


def plain_text(message, max_chars: int = 262144) -> str:
    """Only actual Plain components, never a guessed media description."""
    parts = []
    for component in getattr(message, "message", []) or []:
        if type(component).__name__ == "Plain":
            text = getattr(component, "text", "")
            if isinstance(text, str):
                parts.append(text)
    result = " ".join(" ".join(parts).split())
    if len(result) > max_chars:
        return ""  # no silent truncation of evidence
    if result.startswith(("[附件未取得", "[图片未取得", "[视频未取得", "[文件传输通知")):
        return ""
    return result


def trusted_sender(event) -> str | None:
    sender = event.get_sender_id()
    return sender if isinstance(sender, str) and ID_PATTERN.fullmatch(sender) else None


def private_owner(event, platform_id: str) -> tuple[str, str] | None:
    if event.get_platform_name() != "wechat_pc_hook" or event.get_platform_id() != platform_id:
        return None
    if event.get_group_id():
        return None
    owner = getattr(event.message_obj, "self_id", "")
    sender = trusted_sender(event)
    if not isinstance(owner, str) or not ID_PATTERN.fullmatch(owner) or not sender or owner == sender:
        return None
    return owner, sender
