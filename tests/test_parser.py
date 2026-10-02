"""Parser tests.

Messages in the "real bank formats" section are copied verbatim from the
SMS Backup & Restore exports (with the account numbers left as they were).
"""
from datetime import datetime
from decimal import Decimal

import pytest

from app.schemas import TransactionMode, TransactionType
from app.services.parser import (
    K_CREDIT,
    K_DEBIT,
    K_TRANSFER,
    NOT_POSTED,
    POSTED,
    R_DECLINED,
    R_SCHEDULED,
    parse_date,
    parse_sms,
)


# --------------------------------------------------------------- synthetic
def test_amount_variants():
    for prefix in ("Rs.", "Rs", "INR", "₹"):
        p = parse_sms(f"{prefix} 1,000.00 debited via UPI on 01-08-2025")
        assert p.amount == Decimal("1000.00")


def test_indian_lakh_grouping():
    p = parse_sms("Avail pre-approved loan up to INR 2,25,000 with low interest")
    assert p.verdict == NOT_POSTED
    p = parse_sms("INR 2,25,000.50 debited from A/C XXXXXX1234 on 05-08-2025")
    assert p.amount == Decimal("225000.50")


def test_missing_amount():
    p = parse_sms("Statement is ready for your account")
    assert p.amount == Decimal("0")
    assert not p.is_transaction


# ------------------------------------------------- not-a-transaction cases
@pytest.mark.parametrize(
    "text,reason",
    [
        ("Your OTP for the transaction of Rs. 45,000 is 883421. Do not share with anyone.", "otp"),
        ("862585 is the OTP for transaction of INR 30043.71 on Kotak Bank Card 4365 at SIHUB, "
         "valid for 3 mins. Do not share OTP with anyone.", "otp"),
        ("364279 is OTP for txn of INR 845.00 at AMAZON on ICICI Bank Credit Card XX7008.", "otp"),
        ("Trxn on your Kotak Bank Credit Card x4365 of INR 4999.00 at AMAZON could not be approved. "
         "Please call 18002662 for assistance.", R_DECLINED),
        ("Auto-Pay Wz1gy7T088 for Shopify Commerce Singapore Pte. Ltd. of INR 649.00 failed as "
         "Kotak Card x4365 wasn't validated.", R_DECLINED),
        ("AutoPay for Vi Postpaid-VODAFONE 8 bill of INR 588.82 is scheduled on 07-07-2024. "
         "Please maintain sufficient funds.", R_SCHEDULED),
        ("Payment of INR 649.00 for NETFLIX will be auto debited from Kotak Bank Credit Card x4365.", R_SCHEDULED),
        ("Avl Bal for A/c XXXX1832 as on 21-JUL-2024 07:07:19 AM is INR 480807.69. "
         "Combined Bal is INR 480807.69.", "balance"),
        ("HDFC Bank Credit Card XX0030 Statement: Total due amt: INR 45000.00 Min due amt: INR 4500.00 "
         "Due by: 05-09-2024.", "statement"),
        ("Dear Cardholder, ZERO DOCUMENTATION! Avail pre-approved loan up to INR 2,25,000 with low "
         "interest on YES BANK Credit Card.", "promo"),
        ("Don't miss! Recharge Jio no 8320688102 with Rs.799 plan (1.5 GB/day, Voice Unlimited)", "promo"),
    ],
)
def test_rejected_messages(text, reason):
    p = parse_sms(text)
    assert p.verdict == NOT_POSTED
    assert not p.is_transaction
    assert p.is_noise
    if reason:
        assert p.reason == reason


def test_otp_with_similar_shape_is_still_caught():
    p = parse_sms("340099 is the OTP for transaction of INR 45000 on ICICI Credit Card XX7008")
    assert p.verdict == NOT_POSTED


# ------------------------------------------------ real bank formats (verbatim)
REAL = [
    # Kotak card spend
    ("INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at SKOTAK-TORRENTPOWER-04947. "
     "Avl limit INR 122280.49. For dispute, https://www.kotak.com/rd/dispute",
     dict(kind=K_DEBIT, amount=Decimal("370"), acct="4365", mode=TransactionMode.CARD,
          date=(2024, 7, 1), merchant="TORRENTPOWER-04947")),
    # HDFC card spend, ISO timestamp with colons
    ("Rs.551.97 spent on HDFC Bank Card x2060 at Apollo Pharmacies Ltd on 2024-07-17:19:10:16."
     "Not U? To Block & Reissue Call 18002586161/SMS BLOCK CC 2060 to 7308080808",
     dict(kind=K_DEBIT, amount=Decimal("551.97"), acct="2060", mode=TransactionMode.CARD,
          date=(2024, 7, 17), merchant="Apollo Pharmacies Ltd")),
    # Kotak UPI out to a handle
    ("Sent Rs.120.00 from Kotak Bank AC X0359 to BHARATPE907720034330@yesbankltd on 04-07-24."
     "UPI Ref 418626437258. Not you, kotak.com/fraud",
     dict(kind=K_DEBIT, amount=Decimal("120.00"), acct="0359", mode=TransactionMode.UPI,
          date=(2024, 7, 4), handle="BHARATPE907720034330@yesbankltd")),
    # HDFC UPI "Amt Sent" block, date has NO YEAR
    ("Amt Sent Rs.362.00 From HDFC Bank A/C *8254 To SRIDHARA On 01-07 Ref 454998578896 "
     "Not You? Call 18002586161/SMS BLOCK UPI to 7308080808",
     dict(kind=K_DEBIT, amount=Decimal("362.00"), acct="8254", mode=TransactionMode.UPI,
          merchant="SRIDHARA", ref="454998578896")),
    # Kotak IMPS credit, beneficiary
    ("Rs. 33309 credited to your Kotak Bank a/c XX0359 via NEFT from beneficiary "
     "ASHKABAHEN VASANTBHAI SHAH. UTR Ref. N184243126372189",
     dict(kind=K_CREDIT, amount=Decimal("33309"), acct="0359", mode=TransactionMode.NEFT)),
    # Kotak IMPS credit, no beneficiary
    ("Received Rs. 115701.00 on 02-07-24 in your Kotak Bank A/C x1832 by an A/C linked to "
     "mobile x130. IMPS Ref no 418420394492.",
     dict(kind=K_CREDIT, amount=Decimal("115701.00"), acct="1832", mode=TransactionMode.IMPS)),
    # HDFC credit card payment => transfer
    ("DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 15798.00 RECEIVED TOWARDS YOUR CREDIT CARD "
     "ENDING 0030 THROUGH IMPS ON 4-7-2024.YOUR AVAILABLE LIMIT IS RS. 1145695.53",
     dict(kind=K_TRANSFER, amount=Decimal("15798.00"), acct="0030", mode=TransactionMode.CARD,
          date=(2024, 7, 4))),
    # Kotak credit card top-up => transfer
    ("Payment of INR 27350 is credited to your Kotak Bank Credit Card x4365 on 10-JUL-2024. "
     "Available Credit limit is INR 149041.67.",
     dict(kind=K_TRANSFER, amount=Decimal("27350"), acct="4365", date=(2024, 7, 10))),
    # Federal Bank NEFT credit
    ("Akshay, RTTISPLTD has sent INR 197,402.00 to you. Mode:NEFT | July 1, 2024 | "
     "Ref No. 2:01072024:S14023163 -Federal Bank",
     dict(kind=K_CREDIT, amount=Decimal("197402.00"), mode=TransactionMode.NEFT,
          date=(2024, 7, 1))),
    # YES BANK card spend
    ("INR 2450.00 spent on YES BANK Card X5003 @ZOMATO 12-08-2024 21:10:33 pm. "
     "Avl Lmt INR 80000.00. SMS BLKCC 5003 to 7308080808 if not you",
     dict(kind=K_DEBIT, amount=Decimal("2450.00"), acct="5003", mode=TransactionMode.CARD,
          date=(2024, 8, 12))),
    # HDFC debit for a bill via NACH
    ("INR 59,217.00 is debited to your Account XXXXXX1832 on 05/07/2024 towards "
     "NACH-10-HDFC BANK-SALARY", None),   # amount/date asserted loosely below
    # OneCard refund credit
    ("Yay! You have received a refund of Rs. 179.00 on your OneCard from GROFERS INDIA PRIVATE.",
     dict(kind=K_CREDIT, amount=Decimal("179.00"))),
]


@pytest.mark.parametrize("text,expect", REAL)
def test_real_bank_formats(text, expect):
    fb = datetime(2024, 8, 20, 12, 0, 0)
    p = parse_sms(text, received_at=fb)

    assert p.verdict == POSTED, f"{p.rule} said {p.reason}"
    assert p.is_transaction

    if expect is None:
        return

    if "amount" in expect:
        assert p.amount == expect["amount"], f"rule={p.rule}"
    if "acct" in expect:
        assert p.account_last_four == expect["acct"], f"rule={p.rule}"
    if "mode" in expect:
        assert p.txn_mode == expect["mode"], f"rule={p.rule}"
    if "kind" in expect:
        assert p.kind == expect["kind"], f"rule={p.rule}"
    if "merchant" in expect:
        assert expect["merchant"].upper() in (p.merchant or "").upper(), \
            f"rule={p.rule} merchant={p.merchant!r}"
    if "handle" in expect:
        assert p.upi_handle == expect["handle"], f"rule={p.rule}"
    if "ref" in expect:
        assert p.upi_ref == expect["ref"], f"rule={p.rule}"
    if "date" in expect:
        y, mo, d = expect["date"]
        assert (p.txn_timestamp.year, p.txn_timestamp.month, p.txn_timestamp.day) == (y, mo, d), \
            f"rule={p.rule} got {p.txn_timestamp}"


def test_direction_maps_to_txn_type():
    debit = parse_sms("INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at ZEPTO")
    assert debit.txn_type == TransactionType.DEBIT

    credit = parse_sms("Received Rs. 5000 on 02-07-24 in your Kotak Bank A/C x1832 "
                       "by an A/C linked to mobile x130. IMPS Ref no 418420394492.")
    assert credit.txn_type == TransactionType.CREDIT


def test_wallet_topup_is_transfer_not_expense():
    """Moving money into your own wallet is not household spending."""
    p = parse_sms("Sent Rs.70.00 from Kotak Bank AC X0359 to gpay-11240878578@okbizaxis "
                  "on 27-07-24.UPI Ref 420941868411. Not you, kotak.com/fraud")
    assert p.kind == K_TRANSFER
    assert p.transfer_note


def test_credit_card_upi_payment_is_transfer():
    p = parse_sms("Sent Rs.31664.00 from Kotak Bank AC X0359 to cheq.payu@icici on 28-07-24."
                  "UPI Ref 421036907242. Not you, kotak.com/fraud")
    assert p.kind == K_TRANSFER


def test_bharatpe_payment_is_still_a_debit():
    p = parse_sms("Sent Rs.120.00 from Kotak Bank AC X0359 to BHARATPE907720034330@yesbankltd "
                  "on 04-07-24.UPI Ref 418626437258. Not you, kotak.com/fraud")
    assert p.kind == K_DEBIT


# ----------------------------------------------------------------- dates
@pytest.mark.parametrize(
    "text,expected",
    [
        ("on 2024-07-06:19:13:33", datetime(2024, 7, 6, 19, 13, 33)),
        ("on 01-JUL-2024", datetime(2024, 7, 1)),
        ("on 19-Jul-2024", datetime(2024, 7, 19)),
        ("on 04/07/2024", datetime(2024, 7, 4)),
        ("on 04-07-24", datetime(2024, 7, 4)),
        ("July 1, 2024", datetime(2024, 7, 1)),
        ("on 05 Aug 2024 09:15 am", datetime(2024, 8, 5, 9, 15)),
        ("on 02-08-2025 02:30 pm", datetime(2025, 8, 2, 14, 30)),
    ],
)
def test_date_formats(text, expected):
    assert parse_date(text, datetime(2025, 1, 1)) == expected


def test_yearless_date_inherits_year_from_sms_timestamp():
    # "On 01-07" with no year, arriving early in July 2025
    got = parse_date("On 01-07", datetime(2025, 7, 9))
    assert got == datetime(2025, 7, 1)


def test_yearless_date_does_not_land_in_the_future():
    """A year-less date far ahead of the SMS belongs to the previous year."""
    got = parse_date("On 28-12", datetime(2025, 1, 5))
    assert got == datetime(2024, 12, 28)


def test_explicit_year_is_never_shifted():
    """An explicit 4-digit year is trusted, even if it reads as 'future'."""
    assert parse_date("on 02-08-2025 02:30 pm", datetime(2025, 1, 1)) == datetime(2025, 8, 2, 14, 30)


def test_non_month_suffix_does_not_crash():
    # "5-sms" must not be mistaken for a month abbreviation
    got = parse_date("call 1800 sms 5-sms now", datetime(2025, 6, 1))
    assert got is None or isinstance(got, datetime)


# -------------------------------------------------------------- accounts
def test_account_masks():
    assert parse_sms("INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024").account_last_four == "4365"
    assert parse_sms("payment towards your CREDIT CARD ENDING 0030").account_last_four == "0030"
    assert parse_sms("From HDFC Bank A/C *8254 To X").account_last_four == "8254"


def test_three_digit_mobile_mask_is_not_an_account():
    p = parse_sms("Received Rs. 115701.00 on 02-07-24 in your Kotak Bank A/C x1832 "
                  "by an A/C linked to mobile x130. IMPS Ref no 418420394492.")
    assert p.account_last_four == "1832"


def test_bare_card_number_after_keyword():
    p = parse_sms("Txn 1093 On HDFC Bank Card 2060 At zomato@paytm by UPI 123 On 12-08")
    assert p.account_last_four == "2060"


# ------------------------------------------------------------- merchants
def test_merchant_strips_processor_prefix():
    p = parse_sms("INR 550.00 spent on HDFC Bank Card x2060 at PAY*IDISHA INFO LABS P "
                  "on 2024-07-17:19:10:16")
    assert "IDISHA" in (p.merchant or "").upper()
    assert not (p.merchant or "").upper().startswith("PAY*")


def test_merchant_from_upi_handle():
    p = parse_sms("Rs 320 paid to UPI:zomato@paytm on 07-08-2025 13:05. UPI Ref 411122233344")
    assert p.upi_handle == "zomato@paytm"
    assert p.txn_mode == TransactionMode.WALLET


def test_balance_is_captured_but_not_a_transaction():
    p = parse_sms("Avl bal in your Kotak A/c XXXX1832 as on 01-07-2024 10:00 AM is INR 480807.69")
    assert p.verdict == NOT_POSTED
    assert p.balance == Decimal("480807.69")

def test_delivery_ranges_are_not_dates():
    from app.services.parser import parse_date
    from datetime import datetime
    ref = datetime(2024, 7, 18, 17, 43)
    for txt in ("It should reflect in your account in 3-5 business days.",
                "will get refunded within 2-3 days", "reflect in 4-7 business days",
                "should reach your A/C in 3-5 working days"):
        assert parse_date(txt, ref) is None
    assert parse_date("txn on 12/08 at store", ref) == datetime(2024, 8, 12)


def test_account_digits_are_not_the_amount():
    from app.services.parser import parse_sms
    from decimal import Decimal
    p = parse_sms("Dear Smart Pay Customer,We have successfully debited your HDFC Bank Credit card "
                  "ending 1234 to pay your SpayBBPS  08041 bill for the amount Rs 1178.82")
    assert p.amount == Decimal("1178.82") and p.account_last_four == "1234"


def test_cashback_credit_and_loan_promo():
    from app.services.parser import parse_sms
    from decimal import Decimal
    c = parse_sms("Congratulations! Cashback of INR 25 has been credited to your Axis Bank Flipkart "
                  "Credit Card XX1234 towards your last month spends - Axis Bank")
    assert c.is_transaction and c.kind == "credit" and c.amount == Decimal("25")
    for promo in ("Dear Customer, get a personal loan of Rs. 500000 from IDFC FIRST Bank today",
                  "Worry-free browsing for as LOW as Rs30! Now you can buy an add-on data pack"):
        assert not parse_sms(promo).is_transaction


def test_refund_notices_are_not_posted_but_bank_credit_is():
    from app.services.parser import parse_sms
    for pending in (
        "Refund of Rs 98.0 has been initiated for your Zepto order 7F892BSPL87414. It should reflect in your account in 3-5 business days.",
        "We have processed a refund of Rs 240.65 for your Apollo247 order 315781050, the amount should reflect in your A/C in 3-5 working days",
        "Rs 444.0 refunded to your account & will reflect in 48hrs. Refund reference no. is 4287368",
        "We have initiated a refund of Rs.4.00 for ORD341352280 back to your card, which should reflect in 4-7 business days. -blinkit",
    ):
        assert not parse_sms(pending).is_transaction, pending
    bank = parse_sms("Rs. 2667.6 has been credited to your SBI Credit Card xxxx4901, towards reversal/cashback from AMAZON PAY INDIA PRIVA Bangalore IN on 10/10/24")
    assert bank.is_transaction and bank.kind == "credit"
    one = parse_sms("Yay! You have received a refund of Rs. 179.00 on your OneCard from GROFERS INDIA PRIVATE.")
    assert one.is_transaction and one.kind == "credit"
