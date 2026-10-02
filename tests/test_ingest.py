"""End-to-end ingestion: prefixed Telegram message -> categorised transaction."""
import pytest
from sqlalchemy import select

from app.models import (
    Category,
    Person,
    RawMessage,
    Transaction,
    TransactionAllocation,
)
from app.schemas import AllocationType, TransactionType
from app.services.seed import seed_categories, seed_persons
from app.services.telegram import TelegramProcessor

pytestmark = pytest.mark.asyncio


async def _seed(session):
    await seed_persons(session)
    await seed_categories(session)
    await session.commit()


async def test_prefix_creates_person_and_allocation(session):
    await _seed(session)

    sms = ("HDFC Bank: Rs. 1,250.50 debited from A/C XXXXXX8899 via UPI to SWIGGY "
           "on 12-08-2025 14:32, UPI Ref No 523418972651. Avl bal Rs. 45,230.75")
    txn, note = await TelegramProcessor(session).process(f"AS- {sms}", 1001)

    assert txn is not None
    assert note == "ok"
    assert float(txn.parsed_amount) == 1250.50
    assert txn.txn_type == TransactionType.DEBIT
    assert txn.txn_mode.value == "upi"
    assert txn.merchant and "swiggy" in txn.merchant.lower()

    # Paid By derived from the AS- prefix
    person = await session.get(Person, txn.person_id)
    assert person.prefix == "AS-"
    assert person.name == "Ashka Shah"

    # Auto-categorised by keyword rule
    assert txn.category_id is not None
    assert txn.needs_review is False

    # Default allocations: paid_by + expense_for, both the payer
    allocs = (
        await session.execute(
            select(TransactionAllocation).where(TransactionAllocation.transaction_id == txn.id)
        )
    ).scalars().all()
    kinds = {a.allocation_type for a in allocs}
    assert AllocationType.PAID_BY in kinds
    assert AllocationType.EXPENSE_FOR in kinds
    assert all(a.person_id == person.id for a in allocs)


async def test_duplicate_message_id_is_idempotent_guard(session):
    """Re-delivery of the same Telegram message must not create a second row."""
    await _seed(session)
    sms = "Rs 500 debited via UPI to Zomato on 12-08-2025 10:00"

    first, _ = await TelegramProcessor(session).process(f"AP- {sms}", 2002)
    assert first is not None
    assert first.message_id == 2002

    # Same external id again -> refused at the raw layer
    second, note = await TelegramProcessor(session).process(f"AP- {sms}", 2002)
    assert second is None
    assert note == "already ingested"

    raws = (await session.execute(select(RawMessage))).scalars().all()
    assert len(raws) == 1


async def test_raw_message_is_stored_even_when_not_a_transaction(session):
    """A rejected message must still leave an auditable row."""
    await _seed(session)
    sms = "Your OTP for the transaction of Rs. 45,000 is 883421."

    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 2500)
    assert txn is None

    row = (await session.execute(select(RawMessage))).scalar_one()
    assert row.verdict == "not_posted"
    assert row.reason == "otp"
    assert row.rule
    assert row.transaction_id is None
    assert "45,000" in row.body


async def test_unknown_prefix_is_rejected(session):
    txn, note = await TelegramProcessor(session).process("XX- something", 3003)
    assert txn is None
    assert "no recognised prefix" in note

    # Still archived, so you can see what arrived and why it was refused
    row = (await session.execute(select(RawMessage))).scalar_one()
    assert row.prefix_detected is None
    assert row.transaction_id is None


async def test_keywords_match_whole_words_only(session):
    """"rent" is a substring of "torrent" - Torrent Power is not rent."""
    from app.services.categorizer import _contains_phrase

    assert not _contains_phrase("torentpower", "rent")
    assert not _contains_phrase("torrent power-lightbil", "rent")
    assert _contains_phrase("torrent power-lightbil", "torrent power")
    assert _contains_phrase("house rent", "rent")
    assert _contains_phrase("rent for october", "rent")

    # A few UPI handles are legitimately part of a larger token
    assert _contains_phrase("sent to gpay-11240878578@okbizaxis", "gpay-")
    assert _contains_phrase("cheq.payu@icici", "cheq.payu")
    assert not _contains_phrase("plain gpay transfer", "gpay-")


async def test_torrent_power_is_not_rent(session):
    await _seed(session)
    sms = ("INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at "
           "SKOTAK-TORRENTPOWER-04947. Avl limit INR 122280.49")
    txn, _ = await TelegramProcessor(session).process(f"AP- {sms}", 6003)

    assert txn is not None
    cat = await session.get(Category, txn.category_id)
    # The merchant is "TORRENTPOWER-04947" after prefix stripping, so this stays
    # uncategorised - critically, it must NOT land in Rent.
    assert cat is None or cat.name != "Rent"


async def test_otp_is_skipped_entirely(session):
    """OTPs are not transactions - they must not be stored at all."""
    await _seed(session)
    sms = "Your OTP for the transaction of Rs. 45,000 is 883421. Do not share with anyone."
    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 4004)

    assert txn is None
    assert "otp" in note
    rows = (await session.execute(select(Transaction))).scalars().all()
    assert rows == []


async def test_scheduled_autopay_is_not_counted_as_spend(session):
    """A future autopay reminder would double-count the eventual debit."""
    await _seed(session)
    sms = ("AutoPay for Vi Postpaid-VODAFONE 8 bill of INR 588.82 is scheduled on 07-07-2024. "
           "Please maintain sufficient funds in your Kotak Bank(A/c or Credit card) x4365.")
    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 4005)

    assert txn is None
    assert "scheduled" in note
    rows = (await session.execute(select(Transaction))).scalars().all()
    assert rows == []


async def test_declined_card_is_not_counted_as_spend(session):
    await _seed(session)
    sms = ("Trxn on your Kotak Bank Credit Card x4365 of INR 4999.00 at AMAZON could not be "
           "approved. Please call 18002662 for assistance.")
    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 4006)

    assert txn is None
    assert "declined" in note


async def test_credit_card_payment_becomes_a_transfer(session):
    """CC payments reduce card outstanding; they are not household spending."""
    await _seed(session)
    sms = ("DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 15798.00 RECEIVED TOWARDS YOUR CREDIT CARD "
           "ENDING 0030 THROUGH IMPS ON 4-7-2024.YOUR AVAILABLE LIMIT IS RS. 1145695.53")
    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 4007)

    assert txn is not None
    cat = await session.get(Category, txn.category_id)
    assert cat.type.value == "transfer"
    assert cat.name == "Credit Card Payment"

    # A transfer gets Paid By but no Expense For row
    allocs = (await session.execute(
        select(TransactionAllocation).where(TransactionAllocation.transaction_id == txn.id)
    )).scalars().all()
    assert {a.allocation_type for a in allocs} == {AllocationType.PAID_BY}


async def test_wallet_topup_is_a_transfer(session):
    await _seed(session)
    sms = ("Sent Rs.70.00 from Kotak Bank AC X0359 to gpay-11240878578@okbizaxis on 27-07-24."
           "UPI Ref 420941868411. Not you, kotak.com/fraud")
    txn, _ = await TelegramProcessor(session).process(f"AP- {sms}", 4008)

    assert txn is not None
    cat = await session.get(Category, txn.category_id)
    assert cat.type.value == "transfer"


async def test_unmapped_merchant_needs_review(session):
    await _seed(session)
    sms = "Rs 777.77 debited from A/C XXXXXX1111 via UPI to Zqxvb Unknown Store on 12-08-2025 12:00"
    txn, note = await TelegramProcessor(session).process(f"AP- {sms}", 5005)

    assert txn is not None
    assert txn.needs_review is True
    assert txn.category_id is None
    assert note == "needs review"


async def test_learned_merchant_auto_categorises_second_time(session):
    await _seed(session)
    processor = TelegramProcessor(session)

    # First message: unknown merchant -> manual review state
    sms1 = "Rs 640 debited via UPI to Zqxvb Unknown Store on 12-08-2025 12:00"
    txn1, _ = await processor.process(f"AP- {sms1}", 6001)
    assert txn1.needs_review is True

    # Simulate the user assigning a category (this learns the mapping)
    target = await session.execute(
        select(Category).where(Category.name == "Shopping")
    )
    shopping = target.scalar_one()
    txn1.category_id = shopping.id
    txn1.needs_review = False
    await processor.categorizer.learn(txn1.merchant, shopping.id, confidence=100)
    await session.commit()

    # Second message from the same merchant -> auto-categorised
    sms2 = "Rs 640 debited via UPI to Zqxvb Unknown Store on 13-08-2025 12:00"
    txn2, note = await processor.process(f"AP- {sms2}", 6002)
    assert txn2.category_id == shopping.id
    assert txn2.needs_review is False
    assert note == "ok"


async def test_income_creates_income_from_allocation(session):
    await _seed(session)
    sms = "HDFC: Rs. 85,000.00 credited to A/C XXXXXX8899 on 01-09-2025 towards salary"
    txn, _ = await TelegramProcessor(session).process(f"AP- {sms}", 7007)

    assert txn.txn_type == TransactionType.CREDIT
    allocs = (
        await session.execute(
            select(TransactionAllocation).where(TransactionAllocation.transaction_id == txn.id)
        )
    ).scalars().all()
    assert AllocationType.INCOME_FROM in {a.allocation_type for a in allocs}
    assert AllocationType.EXPENSE_FOR not in {a.allocation_type for a in allocs}

@pytest.mark.asyncio
async def test_ingest_raw_survives_unique_violation(session):
    """A concurrent retry that loses the race returns None instead of raising."""
    from app.services.telegram import TelegramProcessor
    p = TelegramProcessor(session)
    first = await p.ingest_raw("AP- Rs 10 debited at X", external_id=991, meta={})
    assert first is not None
    # Simulate the race: bypass the pre-check so the insert itself collides.
    orig = session.execute
    calls = {"n": 0}

    async def skip_precheck(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # the duplicate pre-check inside ingest_raw
            class R:
                def scalar_one_or_none(self): return None
            return R()
        return await orig(*a, **k)

    session.execute = skip_precheck
    try:
        assert await p.ingest_raw("AP- Rs 10 debited at X", external_id=991, meta={}) is None
    finally:
        session.execute = orig
