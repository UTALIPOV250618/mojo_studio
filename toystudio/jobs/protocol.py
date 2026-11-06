"""Worker -> GUI wire protocol: one JSON object per stdout line. No Qt imports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Progress:
    stage: str
    done: float
    total: float
    media_id: str | None = None


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Done:
    pass


@dataclass(frozen=True)
class Failed:
    code: str
    detail: str


Message = Progress | Event | Done | Failed


def encode(msg: Message) -> bytes:
    if isinstance(msg, Progress):
        obj: dict[str, Any] = {
            "t": "progress",
            "stage": msg.stage,
            "done": msg.done,
            "total": msg.total,
            "media_id": msg.media_id,
        }
    elif isinstance(msg, Event):
        obj = {"t": "event", "kind": msg.kind, "payload": msg.payload}
    elif isinstance(msg, Done):
        obj = {"t": "done"}
    elif isinstance(msg, Failed):
        obj = {"t": "failed", "code": msg.code, "detail": msg.detail}
    else:
        raise TypeError(f"not a protocol message: {msg!r}")
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> Message:
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"malformed protocol line: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("protocol line is not an object")  # noqa: TRY004
    t = obj.get("t")
    try:
        if t == "progress":
            return Progress(obj["stage"], obj["done"], obj["total"], obj.get("media_id"))
        if t == "event":
            return Event(obj["kind"], obj.get("payload", {}))
        if t == "done":
            return Done()
        if t == "failed":
            return Failed(obj["code"], obj["detail"])
    except KeyError as e:
        raise ValueError(f"missing field {e}") from e
    raise ValueError(f"unknown message type: {t!r}")
