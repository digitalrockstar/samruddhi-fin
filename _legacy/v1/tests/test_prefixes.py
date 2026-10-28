"""Forwarding-format detection.

Covers the shapes that actually arrive in the group chat:
    "AP- <sms>" / "AS- <sms>"
    "AS: New SMS: <sms>"
    "From: +91...\nTime: 14:32\n<sms>"      (no explicit prefix)
"""
import pytest

from app.services.telegram import (
    ALIASES,
    DEFAULT_PREFIX,
    ENVELOPE_MARKERS,
    TelegramProcessor,
)

pytestmark = pytest.mark.asyncio

SMS = "Rs.120.00 from Kotak Bank AC X0359 to SWIGGY@paytm on 04-07-24"


async def detect(session, text):
    """Run prefix detection only (no DB writes)."""
    return await TelegramProcessor(session)._split_prefix(text)


# ------------------------------------------------------------ explicit
@pytest.mark.parametrize(
    "prefixed",
    [
        f"AP- {SMS}",
        f"AS- {SMS}",
        f"AS: {SMS}",
        f"AP: {SMS}",
    ],
)
async def test_explicit_prefixes(session, prefixed):
    person, body, hint = await detect(session, prefixed)
    assert person is not None
    assert body == SMS
    assert hint is None


async def test_as_colon_maps_to_ashka(session):
    person, body, _ = await detect(session, f"AS: {SMS}")
    assert person.prefix == "AS-"
    assert person.name == "Ashka Shah"


async def test_ap_maps_to_akshay(session):
    person, _, _ = await detect(session, f"AP- {SMS}")
    assert person.prefix == "AP-"
    assert person.name == "Akshay Patel"


# ------------------------------------------------------- share-sheet wrap
@pytest.mark.parametrize("marker", ENVELOPE_MARKERS)
async def test_envelope_marker_is_stripped(session, marker):
    person, body, _ = await detect(session, f"AS: {marker}{SMS}")
    assert person.prefix == "AS-"
    assert body == SMS


async def test_envelope_marker_after_prefix_with_newlines(session):
    person, body, _ = await detect(session, f"AS-\nNew SMS:\n{SMS}")
    assert person.prefix == "AS-"
    assert body == SMS


async def test_envelope_without_any_attribution_is_rejected(session):
    """A bare "New SMS:" says nothing about who paid.

    Rejecting is deliberate: silently attributing it to the default person would
    quietly put someone's spending on the wrong side of the ledger.
    """
    person, body, _ = await detect(session, f"New SMS: {SMS}")
    assert person is None
    assert body == SMS, "body is still returned so the caller can log it"


# ------------------------------------------------------- From/Time header
async def test_from_time_header_maps_to_ap(session):
    text = f"From: +919812345678\nTime: 14:32\n{SMS}"
    person, body, hint = await detect(session, text)

    assert person.prefix == DEFAULT_PREFIX == "AP-"
    assert body == SMS, "the From:/Time: header must not reach the parser"
    assert hint == "14:32"


async def test_from_header_with_12_hour_time(session):
    text = f"From: +919812345678\nTime: 2:05 PM\n{SMS}"
    _, _, hint = await detect(session, text)
    assert hint == "14:05"


async def test_from_header_no_time_line(session):
    text = f"From: +919812345678\n{SMS}"
    person, body, hint = await detect(session, text)
    assert person.prefix == DEFAULT_PREFIX
    assert body == SMS
    assert hint is None


async def test_explicit_prefix_beats_from_header(session):
    """AS: with a From: header must still be Ashka."""
    text = f"AS: From: +919999999999\nTime: 09:00\n{SMS}"
    person, body, _ = await detect(session, text)
    assert person.prefix == "AS-"
    assert body == SMS


# ------------------------------------------------------------- rejection
@pytest.mark.parametrize("text", ["XX- hello", "just some text", "", "   "])
async def test_unrecognised_input_is_rejected(session, text):
    person, body, _ = await detect(session, text)
    assert person is None


async def test_empty_body_after_headers_is_rejected(session):
    """A forward that is nothing but headers carries no SMS."""
    txn, note = await TelegramProcessor(session).process(
        "From: +919812345678\nTime: 14:32\n", 9200)
    assert txn is None
    # It is archived (headers were recognised) but yields no transaction.
    assert "not posted" in note or "no sms body" in note


async def test_phone_number_in_header_is_not_mistaken_for_data(session):
    """The From: header contains digits; it must be stripped before parsing."""
    text = "From: +919812345678\nTime: 14:32\n" + SMS
    _, body, _ = await detect(session, text)
    assert "919812345678" not in body
    assert "14:32" not in body


# --------------------------------------------------------------- end-to-end
async def test_time_header_fills_in_a_missing_time(session):
    from app.services.seed import seed_categories, seed_persons

    await seed_persons(session)
    await seed_categories(session)
    await session.commit()

    # The SMS body has a date but no time; the header supplies 14:32.
    text = (
        "From: +919812345678\nTime: 14:32\n"
        "HDFC Bank: Rs. 1,250.50 debited from A/C XXXXXX8899 via UPI to SWIGGY "
        "on 12-08-2025"
    )
    txn, note = await TelegramProcessor(session).process(text, 9101)

    assert txn is not None, note
    assert txn.txn_timestamp.hour == 14
    assert txn.txn_timestamp.minute == 32
    assert float(txn.parsed_amount) == 1250.50


async def test_body_time_wins_over_header(session):
    from app.services.seed import seed_categories, seed_persons

    await seed_persons(session)
    await seed_categories(session)
    await session.commit()

    text = (
        "From: +919812345678\nTime: 09:00\n"
        "INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 21:10 at ZEPTO"
    )
    txn, _ = await TelegramProcessor(session).process(text, 9102)

    assert txn is not None
    # 21:10 comes from the message; the 09:00 header must not override it.
    assert txn.txn_timestamp.hour == 21


async def test_aliases_map_only_to_known_people(session):
    assert set(ALIASES.values()) == {"AP-", "AS-"}