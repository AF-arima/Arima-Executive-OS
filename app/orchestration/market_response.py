from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.orchestration.schemas import ExecutedAction

_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06ff]")
_PERSIAN_MARKERS = re.compile(r"[\u067e\u0686\u0698\u06af]")
_PERSIAN_WORDS = {"سلام", "تو", "کی", "هستی", "فارسی", "چطور", "ممنون", "خوبی"}
_ARABIC_WORDS = {"مرحبا", "أنت", "انت", "كيف", "هذا"}
_ARABIC_MARKERS = re.compile(r"[أإآةى]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_TURKISH_MARKERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = {"sen", "kimsin", "nasıl", "nedir", "merhaba"}
_REQUESTED_LANGUAGE_PHRASES = {
    "fa": ("in persian", "in farsi", "persian language", "farsi language", "به فارسی"),
    "ru": ("in russian", "russian language", "по-русски", "на русском"),
    "en": ("in english", "english language", "на английском"),
}


def _requested_language(text: str) -> str | None:
    normalized = " ".join(text.casefold().split())
    for language, phrases in _REQUESTED_LANGUAGE_PHRASES.items():
        if any(phrase in normalized for phrase in phrases):
            return language
    return None


def detect_response_language(text: str) -> str:
    """Return the response language required by the user's current request."""
    requested = _requested_language(text)
    if requested is not None:
        return requested
    words = set(re.findall(r"[\w\u0600-\u06ff]+", text.casefold()))
    if _PERSIAN_MARKERS.search(text) or words.intersection(_PERSIAN_WORDS):
        return "fa"
    if _ARABIC_SCRIPT.search(text) and (
        words.intersection(_ARABIC_WORDS) or _ARABIC_MARKERS.search(text)
    ):
        return "ar"
    if _HAN.search(text):
        return "zh"
    if _CYRILLIC.search(text):
        return "ru"
    if _TURKISH_MARKERS.search(text) or words.intersection(_TURKISH_WORDS):
        return "tr"
    return "en"


def market_response(
    actions: list[ExecutedAction], *, language: str
) -> str | None:
    """Build a deterministic, evidence-backed response for market requests."""
    action = next(
        (item for item in actions if item.name == "market.current_price"),
        None,
    )
    if action is None:
        return None

    data = action.output.get("data") if action.success else None
    if not _verified_quote(data):
        return _unavailable(language)

    assert isinstance(data, dict)
    evidence = data["evidence"]
    assert isinstance(evidence, dict)
    price = str(data["price"])
    provider = str(data["provider"])
    evidence_id = str(evidence["evidence_id"])
    if language == "fa":
        return (
            f"قیمت تأییدشدهٔ BTC/USD برابر {price} دلار است؛ "
            f"این داده توسط {provider} ارائه شده است. [evidence:{evidence_id}]"
        )
    return (
        f"The verified BTC/USD price is {price} USD, provided by "
        f"{provider}. [evidence:{evidence_id}]"
    )


def _verified_quote(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    required = (
        data.get("price"),
        data.get("provider"),
        data.get("source"),
        data.get("verification_state"),
    )
    if not all(isinstance(item, str) and item.strip() for item in required):
        return False
    if data["verification_state"] != "verified_customer_display":
        return False
    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        return False
    if not all(
        isinstance(evidence.get(key), str) and evidence[key].strip()
        for key in ("evidence_id", "content")
    ):
        return False
    try:
        return Decimal(str(data["price"])) > 0
    except (InvalidOperation, ValueError):
        return False


def _unavailable(language: str) -> str:
    if language == "fa":
        return "قیمت تأییدشدهٔ بیت‌کوین در حال حاضر در دسترس نیست."
    return "A verified BTC price is currently unavailable."
