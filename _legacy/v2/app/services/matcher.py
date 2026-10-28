import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from app.models import LearnedFormat

@dataclass
class Match:
    fmt: LearnedFormat
    groups: dict[str, str]
    score: int

def normalise(text: str) -> str:
    text = re.sub(r"https?://\S+", "<URL>", text)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "<DATE>", text)
    text = re.sub(r"\b\d[\d,]*\b", "<NUM>", text)
    return text.strip().lower()

def match_formats(text: str, formats: list[LearnedFormat]) -> list[Match]:
    matches = []
    for fmt in formats:
        if not fmt.enabled:
            continue
        try:
            m = re.search(fmt.pattern, text, re.I | re.S)
        except re.error:
            continue
        if m:
            matches.append(Match(fmt, m.groupdict(), fmt.specificity))
    return sorted(matches, key=lambda x: x.score, reverse=True)

def parse_amount(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
