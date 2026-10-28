"""SMS format registry: one decision per message format, made once by a person.

A format is the first few words of the message with numbers masked. Unknown formats
(no parser rule matched) wait in a queue instead of being booked as transactions.
"""
import re
from typing import Optional

from app.services.parser import K_CREDIT, K_DEBIT, K_TRANSFER, NOT_POSTED, POSTED, ParsedSMS
from app.schemas import TransactionMode, TransactionType

DECISIONS = {"ignore", "debit", "credit", "transfer"}
_URL = re.compile(r"https?://\S+|\b\w+\.\w{2,}/\S*")


def skeleton(body: str, words: int = 8) -> str:
    text = _URL.sub(" ", body or "")
    text = re.sub(r"\d[\d,.]*", "#", text)
    toks = re.sub(r"[^\w#@]+", " ", text.lower()).split()
    return " ".join(toks[:words])[:120]


def mask(body: str) -> str:
    """Safe-to-display text: long digit runs shortened to their last two digits."""
    return re.sub(r"\d{5,}", lambda m: "#" * (len(m.group()) - 2) + m.group()[-2:], " ".join((body or "").split()))


def apply_decision(parsed: ParsedSMS, decision: Optional[str], mode: Optional[str] = None) -> ParsedSMS:
    """Force the verdict a person chose for this format."""
    if decision not in DECISIONS:
        return parsed
    if decision == "ignore":
        parsed.verdict, parsed.reason, parsed.is_transaction = NOT_POSTED, "manual_ignore", False
        return parsed
    kind = {"debit": K_DEBIT, "credit": K_CREDIT, "transfer": K_TRANSFER}[decision]
    parsed.verdict, parsed.reason, parsed.kind = POSTED, None, kind
    parsed.is_transaction = parsed.amount is not None and parsed.amount > 0
    parsed.txn_type = TransactionType.CREDIT if kind == K_CREDIT else TransactionType.DEBIT
    if mode:
        try:
            parsed.txn_mode = TransactionMode(mode)
        except ValueError:
            pass
    return parsed
