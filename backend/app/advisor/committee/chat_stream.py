from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

ChatRole = Literal[
    "data",
    "fundamental",
    "technical",
    "news",
    "quant",
    "bull",
    "bear",
    "trader",
    "backtest",
    "risk",
    "chair",
]
ChatStatus = Literal["streaming", "completed", "degraded", "failed"]

_NODE_ROLE: dict[str, ChatRole] = {
    "prepare": "data",
    "backtest": "backtest",
    "risk": "risk",
}

_ESCAPE_MAP: dict[str, str] = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
}

_SURROGATE_HIGH_MIN = 0xD800
_SURROGATE_HIGH_MAX = 0xDBFF
_SURROGATE_LOW_MIN = 0xDC00
_SURROGATE_LOW_MAX = 0xDFFF


class CardRef(BaseModel):
    model_config = {"extra": "forbid"}

    attempt: int = Field(ge=1)
    node: str = Field(min_length=1)
    kind: str = Field(min_length=1)


class ChatMessagePayload(BaseModel):
    model_config = {"extra": "forbid"}

    message_id: str
    role: ChatRole
    node: str
    round: int | None = None
    content: str
    status: ChatStatus
    sequence: int = Field(ge=0)
    generation: int = Field(default=1, ge=1)
    created_at: str | None = None
    completed_at: str | None = None
    card_kind: str | None = None
    card_ref: CardRef | None = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    event_type: Literal["message_started", "message_delta"]
    payload: dict[str, Any]


def message_id_for(
    run_id: str,
    attempt: int,
    node: str,
    round_index: int | None = None,
) -> str:
    stable = f"{run_id}:{attempt}:{node}:{round_index or 0}"
    return hashlib.sha256(stable.encode()).hexdigest()[:24]


def system_message(
    *,
    run_id: str,
    attempt: int,
    node: str,
    sequence: int,
    content: str,
    card_kind: str | None = None,
    round_index: int | None = None,
) -> ChatMessagePayload:
    role = _NODE_ROLE[node]
    card_ref = None
    if card_kind is not None:
        card_ref = CardRef(attempt=attempt, node=node, kind=card_kind)
    return ChatMessagePayload(
        message_id=message_id_for(run_id, attempt, node, round_index),
        role=role,
        node=node,
        round=round_index,
        content=content,
        status="completed",
        sequence=sequence,
        card_kind=card_kind,
        card_ref=card_ref,
    )


@dataclass
class IncrementalChatMessageParser:
    compatible: bool = True
    _state: str = field(default="seek_object", init=False)
    _key: str = field(default="", init=False)
    _unicode_hex: str = field(default="", init=False)
    _pending_high_surrogate: int | None = field(default=None, init=False)
    _done: bool = field(default=False, init=False)

    def feed(self, text: str) -> tuple[str, ...]:
        decoded: list[str] = []
        for char in text:
            if self._done:
                break
            piece = self._feed_char(char)
            if piece:
                decoded.append(piece)
        if decoded:
            return ("".join(decoded),)
        return ()

    def _fail(self) -> None:
        self.compatible = False
        self._done = True

    def _feed_char(self, char: str) -> str | None:
        if self._state == "seek_object":
            if char.isspace():
                return None
            if char == "{":
                self._state = "key_open"
                return None
            self._fail()
            return None

        if self._state == "key_open":
            if char.isspace():
                return None
            if char == '"':
                self._key = ""
                self._state = "read_key"
                return None
            self._fail()
            return None

        if self._state == "read_key":
            if char == '"':
                if self._key == "chat_message":
                    self._state = "after_key"
                else:
                    self._fail()
                return None
            self._key += char
            return None

        if self._state == "after_key":
            if char.isspace():
                return None
            if char == ":":
                self._state = "value_open"
                return None
            self._fail()
            return None

        if self._state == "value_open":
            if char.isspace():
                return None
            if char == '"':
                self._state = "in_value"
                return None
            self._fail()
            return None

        if self._state == "in_value":
            if char == '"':
                if self._pending_high_surrogate is not None:
                    self._fail()
                    return None
                self._done = True
                return None
            if self._pending_high_surrogate is not None:
                if char == "\\":
                    self._state = "escape"
                    return None
                self._fail()
                return None
            if char == "\\":
                self._state = "escape"
                return None
            return char

        if self._state == "escape":
            return self._resolve_escape(char)

        if self._state == "unicode":
            return self._consume_unicode(char)

        return None

    def _resolve_escape(self, char: str) -> str | None:
        if self._pending_high_surrogate is not None:
            if char == "u":
                self._unicode_hex = ""
                self._state = "unicode"
                return None
            self._fail()
            return None
        if char == "u":
            self._unicode_hex = ""
            self._state = "unicode"
            return None
        mapped = _ESCAPE_MAP.get(char)
        if mapped is None:
            self._fail()
            return None
        self._state = "in_value"
        return mapped

    def _consume_unicode(self, char: str) -> str | None:
        if char not in "0123456789abcdefABCDEF":
            self._fail()
            return None
        self._unicode_hex += char
        if len(self._unicode_hex) < 4:
            return None
        codepoint = int(self._unicode_hex, 16)
        self._unicode_hex = ""
        self._state = "in_value"
        return self._emit_codepoint(codepoint)

    def _emit_codepoint(self, codepoint: int) -> str | None:
        if _SURROGATE_HIGH_MIN <= codepoint <= _SURROGATE_HIGH_MAX:
            if self._pending_high_surrogate is not None:
                self._fail()
                return None
            self._pending_high_surrogate = codepoint
            return None

        if _SURROGATE_LOW_MIN <= codepoint <= _SURROGATE_LOW_MAX:
            if self._pending_high_surrogate is None:
                self._fail()
                return None
            high = self._pending_high_surrogate
            self._pending_high_surrogate = None
            combined = (
                0x10000
                + ((high - _SURROGATE_HIGH_MIN) << 10)
                + (codepoint - _SURROGATE_LOW_MIN)
            )
            return chr(combined)

        if self._pending_high_surrogate is not None:
            self._fail()
            return None
        return chr(codepoint)
