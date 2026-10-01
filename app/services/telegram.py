"""Turns a forwarded Telegram message into a Transaction."""
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Person,
    RawMessage,
    Transaction,
    TransactionAllocation,
)
from app.schemas import AllocationType, CashbackStatus, CategoryType, TransactionType
from app.services.categorizer import Categorizer
from app.services.parser import (
    K_CREDIT,
    K_DEBIT,
    K_TRANSFER,
    NOT_POSTED,
    POSTED,
    ParsedSMS,
    parse_sms,
)

# Bumped whenever parsing behaviour changes, so `reprocess` can tell which
# stored messages were derived by an older version of the rules.
PARSER_VERSION = "2026-10-01.1"


def _has_clock(dt: Optional[datetime]) -> bool:
    return bool(dt and (dt.hour or dt.minute or dt.second))


def _apply_clock(dt: Optional[datetime], hint: Optional[datetime]) -> Optional[datetime]:
    if not dt or not hint:
        return dt
    try:
        return dt.replace(hour=hint.hour, minute=hint.minute, second=hint.second)
    except (ValueError, TypeError):
        return dt


def _hint_to_datetime(
    hint: Optional[str], received_at: Optional[datetime]
) -> Optional[datetime]:
    """Turn a "Time: 14:32" header into a datetime on the receipt date."""
    if not hint:
        return None
    try:
        hh, mm, *rest = hint.split(":")
        base = (received_at or datetime.now()).replace(
            hour=int(hh), minute=int(mm), second=int(rest[0]) if rest else 0,
            microsecond=0,
        )
        return base
    except (ValueError, TypeError):
        return None


def _cache_parse(row: RawMessage, parsed: ParsedSMS) -> None:
    """Record the parser's verdict on the raw row."""
    row.verdict = parsed.verdict or NOT_POSTED
    row.reason = parsed.reason
    row.rule = parsed.rule
    row.amount = parsed.amount
    row.txn_type = parsed.txn_type if parsed.verdict == POSTED else None
    row.txn_mode = parsed.txn_mode
    row.account_last_four = parsed.account_last_four
    row.merchant = parsed.merchant
    row.upi_ref = parsed.upi_ref
    row.upi_handle = parsed.upi_handle
    row.transfer_kind = parsed.kind or None
    row.confidence = parsed.confidence
    row.parsed_at = datetime.now()
    row.parser_version = PARSER_VERSION

# Canonical person prefixes -> (name, display_order)
PREFIX_MAP = {
    "AP-": ("Akshay Patel", 1),
    "AS-": ("Ashka Shah", 2),
}

# Accepted spellings, normalised to a canonical PREFIX_MAP key.
ALIASES = {
    "AP-": "AP-", "AP:": "AP-", "AP": "AP-",
    "AS-": "AS-", "AS:": "AS-", "AS": "AS-",
}

# Wrappers some phone share-sheets add. Stripped after the person is known.
# NOTE: none of these may contain digits we would misread as an amount/account.
ENVELOPE_MARKERS = (
    "New SMS:", "New Message:", "New text message:",
    "SMS:", "Message:", "Forwarded message:",
)

# A header block like:
#   From: +919812345678
#   Time: 14:32
#   <actual sms>
# Each line is consumed in full so the header never leaks into the SMS body,
# with or without a trailing newline.
SENDER_HEADER = re.compile(
    r"^[ \t]*(?:From|Sent from|Contact)[ \t]*:[^\n]*"
    r"(?:\n[ \t]*(?:Time|Date|Date[ /]Time|Timestamp)[ \t]*:[ \t]*(?P<time>[^\n]*))?"
    r"(?:\n[ \t]*)*",
    re.IGNORECASE,
)

# A bare time like "14:32" or "2:05 PM"
TIME_HINT = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$",
                       re.IGNORECASE)

# A header-only forward with no explicit person marker is attributed here.
# This is the "From:/Time: ... " shape, which comes from Akshay's handset.
DEFAULT_PREFIX = "AP-"

# subcategory name to look for when the parser says "this was a transfer"
TRANSFER_LEAF = {
    "credit card": "Credit Card Payment",
    "wallet": "Wallet Top-up",
    "account": "Account Transfer",
    "loan": "Loan Repayment",
    "investment": "Investment Funding",
}


class TelegramProcessor:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.categorizer = Categorizer(db)

    # ------------------------------------------------------------------
    # Step 1 - store the message exactly as it arrived (before any parsing)
    # ------------------------------------------------------------------
    async def ingest_raw(
        self,
        message_text: str,
        external_id: Optional[int],
        meta: Optional[dict] = None,
        source: str = "telegram",
    ) -> Optional[RawMessage]:
        """Persist the inbound message. Idempotent on (source, external_id).

        Returns None if this message was already stored.
        """
        meta = meta or {}

        if external_id is not None:
            existing = (
                await self.db.execute(
                    select(RawMessage).where(
                        RawMessage.source == source,
                        RawMessage.external_id == str(external_id),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return None

        person, body, time_hint = await self._split_prefix(message_text)

        row = RawMessage(
            source=source,
            external_id=str(external_id) if external_id is not None else None,
            chat_id=str(meta["chat_id"]) if meta.get("chat_id") is not None else None,
            sender_user_id=str(meta["sender_id"]) if meta.get("sender_id") is not None else None,
            sender_name=meta.get("sender_name"),
            prefix_detected=person.prefix if person else None,
            body=body,
            raw_text=message_text,
            received_at=meta.get("received_at") or datetime.now(),
            txn_date_hint=_hint_to_datetime(time_hint, meta.get("received_at")),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Step 2 - derive a transaction from a stored message
    # ------------------------------------------------------------------
    async def derive(self, row: RawMessage, replace: bool = False) -> Tuple[Optional[Transaction], str]:
        """Parse a stored message and create/link its transaction.

        With replace=True the existing transaction is deleted first, which is
        what reprocessing uses.
        """
        sms_text = row.body or ""
        if row.prefix_detected is None and not sms_text.strip():
            return None, "no sms body"

        if replace and row.transaction_id:
            old = await self.db.get(Transaction, row.transaction_id)
            if old:
                await self.db.delete(old)
                await self.db.flush()
                row.transaction_id = None

        parsed = parse_sms(sms_text, received_at=row.received_at or datetime.now())
        if row.txn_date_hint and not _has_clock(parsed.txn_timestamp):
            parsed.txn_timestamp = _apply_clock(parsed.txn_timestamp, row.txn_date_hint)

        # Cache the parse either way, so the archive explains itself.
        _cache_parse(row, parsed)

        # Attribution first: without a person we cannot place the money at all,
        # which is a more useful thing to report than a missing amount.
        person = await self._person_for(row)
        if not person:
            row.note = "no recognised prefix"
            await self.db.commit()
            return None, row.note

        if parsed.verdict != POSTED:
            row.note = f"not posted ({parsed.reason})"
            await self.db.commit()
            return None, row.note

        if parsed.amount <= 0:
            row.note = "no amount found"
            await self.db.commit()
            return None, row.note

        txn, note = await self._build_transaction(row, person, parsed)
        row.transaction_id = txn.id if txn else None
        row.note = note
        await self.db.commit()
        return txn, note

    async def preview(self, row: RawMessage) -> Tuple[ParsedSMS, bool, str]:
        """What would derive() do with this row? Writes nothing.

        Used by `reprocess --dry-run`, which must not mutate anything - a plain
        re-parse plus the same gates, with no DB side effects at all.
        """
        sms_text = row.body or ""
        parsed = parse_sms(sms_text, received_at=row.received_at or datetime.now())
        if row.txn_date_hint and not _has_clock(parsed.txn_timestamp):
            parsed.txn_timestamp = _apply_clock(parsed.txn_timestamp, row.txn_date_hint)

        if not await self._person_for(row):
            return parsed, False, "no recognised prefix"
        if parsed.verdict != POSTED:
            return parsed, False, f"not posted ({parsed.reason})"
        if parsed.amount <= 0:
            return parsed, False, "no amount found"
        return parsed, True, "would create transaction"

    async def process(self, message_text: str, message_id: int) -> Tuple[Optional[Transaction], str]:
        """Webhook path: store raw first, then derive."""
        row = await self.ingest_raw(message_text, message_id)
        if row is None:
            return None, "already ingested"
        return await self.derive(row)

    async def _person_for(self, row: RawMessage) -> Optional[Person]:
        if row.prefix_detected and row.prefix_detected in PREFIX_MAP:
            return await self._get_or_create_person(row.prefix_detected)
        return None

    # ------------------------------------------------------------------
    # Step 3 - transaction creation from a parsed message
    # ------------------------------------------------------------------
    async def _build_transaction(self, row, person, parsed) -> Tuple[Optional[Transaction], str]:
        sms_text = row.body or ""
        account = await self._match_account(person, parsed)

        # ---- transfers get their own category root so they do not inflate
        #      household spending (CC payment reduces outstanding, not expense)
        if parsed.kind == K_TRANSFER:
            category_id = await self._transfer_category(sms_text, parsed)
            needs_review = category_id is None
        else:
            category_id = await self.categorizer.categorize(
                sms_text, parsed.merchant, parsed.txn_type
            )
            needs_review = category_id is None

        # message_id mirrors the raw row's external id (the Telegram message id)
        # so existing lookups and CSV exports keep working, while raw_messages
        # remains the authoritative link.
        try:
            message_id = int(row.external_id) if row.external_id else None
        except (TypeError, ValueError):
            message_id = None

        txn = Transaction(
            message_id=message_id,
            person_id=person.id,
            account_id=account.id if account else None,
            category_id=category_id,
            raw_text=sms_text,
            parsed_amount=parsed.amount,
            currency="INR",
            txn_type=parsed.txn_type,
            txn_mode=parsed.txn_mode,
            merchant=parsed.merchant,
            upi_ref=parsed.upi_ref,
            txn_timestamp=parsed.txn_timestamp or row.received_at or datetime.now(),
            is_spam=False,
            needs_review=needs_review,
        )
        self.db.add(txn)
        await self.db.flush()

        # Paid By always comes from the prefix
        self.db.add(TransactionAllocation(
            transaction_id=txn.id,
            person_id=person.id,
            amount=parsed.amount,
            allocation_type=AllocationType.PAID_BY,
            notes="Auto: from SMS prefix",
        ))

        # Default: benefit accrues to whoever paid until edited
        benefit = (AllocationType.INCOME_FROM if parsed.kind == K_CREDIT
                   else AllocationType.EXPENSE_FOR)
        note = ("Auto: default to payer" if parsed.kind in (K_DEBIT, K_CREDIT)
                else "Auto: internal movement")
        if parsed.kind != K_TRANSFER:
            self.db.add(TransactionAllocation(
                transaction_id=txn.id,
                person_id=person.id,
                amount=parsed.amount,
                allocation_type=benefit,
                notes=note,
            ))

        if account and account.default_cashback_pct and parsed.kind == K_DEBIT:
            pct = Decimal(account.default_cashback_pct or 0)
            txn.cashback_amount = (parsed.amount * pct / Decimal("100")).quantize(Decimal("0.01"))
            txn.cashback_type = account.default_cashback_type
            txn.cashback_wallet = account.default_cashback_wallet
            txn.cashback_status = CashbackStatus.PENDING

        await self.db.commit()
        await self.db.refresh(txn)

        if category_id and parsed.merchant and not parsed.transfer_note:
            await self.categorizer.learn(parsed.merchant, category_id, confidence=90)
            await self.db.commit()

        await self.categorizer.detect_recurring(txn)
        await self.db.commit()

        note_out = "needs review" if needs_review else (
            "transfer" if parsed.kind == K_TRANSFER else "ok")
        return txn, note_out
    async def _transfer_category(self, text: str, parsed) -> Optional[int]:
        """Pick the right TRANSFER subcategory for an internal movement."""
        low = text.lower()
        if "credit card" in low or "cardmember" in low or "ending" in low:
            key = "credit card"
        elif "wallet" in low or (parsed.upi_handle and "paytm" in parsed.upi_handle):
            key = "wallet"
        elif "emi" in low or "loan" in low:
            key = "loan"
        elif "fund" in low or "sip" in low or "invest" in low:
            key = "investment"
        else:
            key = "account"
        return await self.categorizer._category_id(
            CategoryType.TRANSFER, TRANSFER_LEAF[key], create_if_missing=True
        )

    async def _split_prefix(self, text: str) -> Tuple[Optional[Person], str, Optional[str]]:
        """Work out who paid and hand back the bare SMS body.

        Recognised shapes:
            "AP- <sms>" / "AS- <sms>"          explicit prefix
            "AS: New SMS: <sms>"               prefix + share-sheet wrapper
            "From: +91...\nTime: 14:32\n<sms>" header block, no prefix

        Returns (person, sms_body, time_hint_or_None).
        """
        body = (text or "").strip()
        if not body:
            return None, "", None

        prefix = None

        # 1. Explicit person marker, longest first so "AS:" beats "AS".
        head = body[:3].upper()
        if head in ALIASES:
            prefix = ALIASES[head]
            body = body[3:].lstrip()

        # 2. A "From:/Time:" header block means Akshay's handset. Check before
        #    stripping so we do not lose the attribution.
        had_header = False
        m = SENDER_HEADER.match(body)
        if m:
            body = body[m.end():].lstrip()
            had_header = True
            if prefix is None:
                prefix = DEFAULT_PREFIX
            time_hint = (m.groupdict().get("time") or "").strip() or None
        else:
            time_hint = None

        # 3. Share-sheet wrappers ("New SMS:", "Forwarded message:").
        for marker in ENVELOPE_MARKERS:
            if body[:len(marker)].lower() == marker.lower():
                body = body[len(marker):].lstrip()
                break

        # 4. A prefix may still be hiding behind the wrapper, e.g. "AS: New SMS: x"
        #    handled in step 1, but be tolerant of "New SMS: AS: x".
        if prefix is None:
            head = body[:3].upper()
            if head in ALIASES:
                prefix = ALIASES[head]
                body = body[3:].lstrip()

        # 5. A header-only forward with no prefix still means Akshay.
        if prefix is None and had_header:
            prefix = DEFAULT_PREFIX

        if prefix is None:
            return None, body, None

        # Normalise a time hint ("2:05 PM") to HH:MM(:SS).
        hint = None
        tm = TIME_HINT.match(time_hint or "")
        if tm:
            hh, mm, ss, ap = tm.groups()
            hour = int(hh)
            if ap:
                if ap.lower().startswith("p") and hour < 12:
                    hour += 12
                elif ap.lower().startswith("a") and hour == 12:
                    hour = 0
            hint = f"{hour:02d}:{mm}" + (f":{ss}" if ss else "")

        person = await self._get_or_create_person(prefix)
        return person, body, hint

    async def _get_or_create_person(self, prefix: str) -> Person:
        found = (
            await self.db.execute(select(Person).where(Person.prefix == prefix))
        ).scalar_one_or_none()
        if found:
            return found

        name, order = PREFIX_MAP[prefix]
        person = Person(name=name, prefix=prefix, display_order=order)
        self.db.add(person)
        await self.db.flush()
        return person

    async def _match_account(self, person: Person, parsed) -> Optional[Account]:
        rows = (
            await self.db.execute(
                select(Account).where(
                    Account.person_id == person.id, Account.is_active.is_(True)
                )
            )
        ).scalars().all()
        if not rows:
            return None

        # Strongest signal: last four digits
        if parsed.account_last_four:
            for a in rows:
                if a.last_four == parsed.account_last_four:
                    return a

        # Next: explicit UPI handle on the account
        if parsed.upi_handle:
            for a in rows:
                if a.upi_id and a.upi_id.lower() == parsed.upi_handle.lower():
                    return a

        # Then: bank name
        if parsed.bank_name:
            for a in rows:
                if (a.bank_name or "").lower() == parsed.bank_name.lower():
                    return a
            for a in rows:
                if parsed.bank_name.lower() in (a.bank_name or "").lower():
                    return a

        return None