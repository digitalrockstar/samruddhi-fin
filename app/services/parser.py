"""Parser for Indian bank SMS, tuned against real exports.

Design
------
1. VERDICT first - every message is classified as POSTED / NOT_POSTED *before*
   any field extraction. Real bank SMS are full of messages that mention an
   amount but are not transactions: OTPs, declined attempts, future autopay
   schedules, balance-only alerts, statement reminders and marketing.
2. RULES - ordered, named templates for the message families these accounts
   actually receive. First match wins, so specific rules precede generic ones.
3. Generic extraction handles anything the rules do not cover.

Every rule carries the field groups it can supply, so extraction is driven by
what matched rather than by re-scanning the whole message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from app.schemas import TransactionMode, TransactionType

# ===========================================================================
# Verdict + kind vocabulary
# ===========================================================================

POSTED = "posted"
NOT_POSTED = "not_posted"
REVIEW = "review"              # no rule recognises this format: wait for a person

K_DEBIT = "debit"          # money left an account you own
K_CREDIT = "credit"        # money arrived
K_TRANSFER = "transfer"    # moved between your own accounts/cards

# Reasons a message is not a transaction (kept for the UI + spam categorisation)
R_OTP = "otp"
R_DECLINED = "declined"
R_SCHEDULED = "scheduled"       # future autopay reminder, money has not moved
R_BALANCE = "balance"           # balance/limit alert only
R_STATEMENT = "statement"       # due-date reminder
R_PROMO = "promo"               # marketing / offers
R_ENVELOPE = "envelope"         # informational, no amount
R_AUTH = "auth"                 # login/verification codes

# ===========================================================================
# Shared patterns
# ===========================================================================

NUM = r"[0-9][0-9,]*(?:\.[0-9]{1,2})?"
# The bare alternation. NEVER embed this directly in a bigger pattern - a
# top-level "|" would bind to the surrounding expression and silently turn the
# rule into "matches any amount". Use AMT instead.
AMT_CORE = rf"(?:rs\.?|inr|₹)\s*({NUM})|({NUM})\s*(?:rs\.?|inr|₹)"
# Group-safe wrapper: adds no capture groups, so group indices are unchanged.
AMT = rf"(?:{AMT_CORE})"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

MONTH_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
# Capturing variant: needed so month group indexes line up with the parser.
MONTH_CAP = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def _month(raw: str) -> int:
    """Map an abbreviation to a month number, raising if it is not one."""
    key = raw.lower()[:3]
    if key not in MONTHS:
        raise ValueError(f"not a month: {raw!r}")
    return MONTHS[key]


# Time suffixes. MUST be a capturing group: the parser reads it as group 4.
TIME = r"(\s*[:\s]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?)?"

DATE_PATTERNS: List[Tuple[str, str]] = [
    # 2024-07-06:19:13:33  /  2024-07-17:19:10:16   (HDFC card spend)
    ("iso", r"(\d{4})-(\d{2})-(\d{2})(?::(\d{2}):(\d{2})(?::(\d{2}))?)?"),
    # 2024/07/06
    ("iso", r"(\d{4})/(\d{2})/(\d{2})"),
    # 01-JUL-2024  /  19-Jul-2024  (Kotak card spend)
    ("dmy_name", rf"(\d{{1,2}})[-/]{MONTH_CAP}[-/](\d{{4}}){TIME}"),
    # 12 Jul 2024  /  5 Aug 2024 09:15 am
    ("dmy_name", rf"(\d{{1,2}})[-\s]+{MONTH_CAP}[-\s,]+(\d{{4}}){TIME}"),
    # 01/07/2024 10:00  /  04-07-2024 13:09
    ("dmy", rf"(\d{{1,2}})[-/](\d{{1,2}})[-/](\d{{4}}){TIME}"),
    # 04-07-24  /  12/08/25
    ("dmy2", rf"(\d{{1,2}})[-/](\d{{1,2}})[-/](\d{{2}}){TIME}"),
    # July 1, 2024  /  Jul 1, 2024 12:00 pm
    ("name_dmy", rf"{MONTH_CAP}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}}){TIME}"),
    # 05-JUL  /  01/JUL  (NO YEAR - year comes from the SMS timestamp)
    ("dmy_guess", rf"(\d{{1,2}})[-/]({MONTH_CAP})"),
    # 12-08  (NO YEAR)
    # Excludes ranges such as "3-5 business days" / "2-3 days" / "4-7 working".
    ("dmy_guess", r"(?<![\d./-])(\d{1,2})[-/](\d{1,2})(?![-/\d])"
                  r"(?!\s*(?:business|working|days?|hrs?|hours?|mins?|minutes?|weeks?|months?|%))"),
]

COMPILED_DATES = [
    (kind, re.compile(rx, re.IGNORECASE)) for kind, rx in DATE_PATTERNS
]

# Accounts are masked in many ways: *8254, x4365, X0359, XX0030, xx8391,
# XXXXXXXXXXXX4365, ending 0030
ACCOUNT_CTX = re.compile(
    r"(?:a/?c|acct|account|acc\.?|card|card\s+no\.?|ending(?:\s+with)?|cc)\s*"
    r"(?:no\.?|number|ending)?\s*[:\-]?\s*"
    r"((?:x+|\*+)\s*-?\s*\d{4}|\d{4})\b",
    re.IGNORECASE,
)
# Any masked tail anywhere (e.g. "Card x4365", "card no.xx8391")
ACCOUNT_MASKED = re.compile(r"(?:x{2,}|\*{1,4})[\s\-]?(\d{4})\b", re.IGNORECASE)
# Bare card number in HDFC/Kotak style: "On HDFC Bank Card 2060", "CC 2060"
ACCOUNT_BARE = re.compile(
    r"(?:card|cc|ending|ending\s+with)\s+(?:no\.?\s*)?(\d{4})\b", re.IGNORECASE
)

UPI_HANDLE = re.compile(r"\b([A-Za-z0-9][\w.\-]{1,}@[A-Za-z][\w.\-]{1,})\b")
UPI_REF = re.compile(
    r"(?:upi\s*ref(?:erence)?(?:\s*no\.?)?|ref\.?\s*no\.?|ref|utr|rrn|"
    r"transaction\s*(?:id|no)|txn\s*(?:id|no)|arn|sn)\s*[:#\-]?\s*([A-Za-z0-9]{8,})",
    re.IGNORECASE,
)

# --------------------------------------------------------------- bank names
BANKS: List[Tuple[str, str]] = [
    ("hdfc bank", "HDFC"), ("hdfc", "HDFC"),
    ("kotak", "Kotak"),
    ("state bank of india", "SBI"), ("sbi", "SBI"),
    ("yes bank", "Yes Bank"),
    ("indusind", "IndusInd"),
    ("axis bank", "Axis"), ("axisbk", "Axis"),
    ("federal bank", "Federal"), ("fedfi", "Federal"),
    ("idfc", "IDFC"), ("first bank", "IDFC First"),
    ("onecard", "OneCard"),
    ("au bank", "AU"), ("uco", "UCO"),
    ("canara", "Canara"), ("union bank", "Union Bank"),
    ("indian bank", "Indian Bank"), ("central bank", "Central Bank"),
    ("bank of baroda", "Bank of Baroda"),
    ("standard chartered", "Standard Chartered"),
    ("citi", "Citi"), ("rbl", "RBL"), ("bandhan", "Bandhan"),
    ("paytm", "Paytm"), ("phonepe", "PhonePe"),
    ("amazon pay", "Amazon Pay"), ("google pay", "Google Pay"), ("gpay", "Google Pay"),
    ("bharatpe", "BharatPe"), ("mobikwik", "MobiKwik"),
    ("freecharge", "FreeCharge"), ("pluxee", "Pluxee"),
    ("airtel", "Airtel"), ("jio", "Jio"), ("vodafone", "Vi"), ("vi care", "Vi"),
]

# ------------------------------------------- handles that are NOT merchants
# Sending money to these is moving your own money around, not spending.
TRANSFER_HANDLES = re.compile(
    r"^(?:cheq\.?payu|cheq1|ccpay|cardpayment|creditcard)"
    r"|gpay-|googlepay|mygate|phonepe|phonpe|wallet|paytmself"
    r"|^(?:.*)@(?:okhdfcbank|okaxis|okicici|ybl|oksbi)$"
    r".*(?:self|own)",
    re.IGNORECASE,
)
# A credit-card payment made through UPI.
CC_PAY_HANDLE = re.compile(
    r"^(?:cheq\.?payu|cheq1|ccpay|cardpayment|creditcardpayment|cardpay)", re.IGNORECASE
)
# Wallet top-ups: moving money into a wallet you then spend from separately.
WALLET_HANDLE = re.compile(
    r"^(?:gpay-[\d]+|googlepay|mygate\.|phonepe|phonpe|walletadd|paytm\.self)", re.IGNORECASE
)

# Payment-processor noise prefixes to strip from merchant names
MERCHANT_JUNK = re.compile(
    r"^(?:PAY\*|POS\*|RAZ\*|COM\w*\*|SP\*|UPI\*|N?A?CL\*|PP\*|IB\w*\*|M\w*\*|"
    r"SBI\w*\*|PAYTM\*|BILL\*|ATM\w*\*|PSKOTAK|SKOTAK|KOTAK-|HDFC-|VOUCHER)",
    re.IGNORECASE,
)

# Generic merchant harvesters. These run only when the matched rule did not
# supply a merchant, and they are what catches long-tail merchants.
MERCHANT_VERB = re.compile(
    r"\b(?:spent|purchased?|booked|charged|paid|debit(?:ed)?|credited)\s+"
    r"(?:on|at|to|towards|for)\s+"
    r"(?:UPI\s*:\s*)?"
    r"([A-Za-z0-9@][A-Za-z0-9@ \w&.\-']{1,60}?)"
    r"(?=\s+(?:on|dated|at|via|using|through|ref|utr|rrn|avl|avail|bal|balance|"
    r"txn|transaction|upi|visa|mastercard|rupay|maestro|amex|credit|debit|a/c|acct|"
    r"card|account|limit|lmt|imps|neft|rtgs|not\s+you|for\s+a\s+period)|[.,;\n]|$)",
    re.IGNORECASE,
)
MERCHANT_AT = re.compile(
    r"\bat\s+([A-Za-z0-9][A-Za-z0-9 \w*.&'/\-]{1,50}?)"
    r"(?=\s+(?:on|dated|for|avl|Avl|\.|$|\n|not\b|for\s+a\s+period))",
    re.IGNORECASE,
)
MERCHANT_FOR = re.compile(
    r"\bfor\s+([A-Za-z0-9][A-Za-z0-9 \w*.&'/\-]{2,50}?)"
    r"(?=\s+(?:on|dated|\.|$|\n|will\s+be|is\s+scheduled|subscription))",
    re.IGNORECASE,
)

MODE_RULES: List[Tuple[TransactionMode, str]] = [
    (TransactionMode.WALLET, r"\bwallet\b|\bpaytm\b|\bphonepe\b|\bgpay\b|"
                              r"\bgoogle\s*pay\b|\bamazon\s*pay\b|\bbharatpe\b|"
                              r"\bmobikwik\b|\bfreecharge\b|\bpluxee\b"),
    (TransactionMode.CARD, r"\b(?:visa|mastercard|rupay|maestro|amex)\b|"
                           r"\bcredit\s*card\b|\bdebit\s*card\b|\bcard\s+(?:no\.?|ending)"),
    (TransactionMode.NETBANKING, r"\bnet\s*banking\b|\binb\b|\bnetbanking\b"),
    (TransactionMode.ATM, r"\batm\b|\batm\s*card\b"),
    (TransactionMode.IMPS, r"\bimps\b"),
    (TransactionMode.NEFT, r"\bneft\b"),
    (TransactionMode.RTGS, r"\brtgs\b"),
    (TransactionMode.UPI, r"\bupi\b|\bupi:\s*[\w.\-]+@[\w.\-]+|@[a-z]{2,}"),
]

# ===========================================================================
# Rule table - ordered, first match wins
# ===========================================================================


@dataclass
class Rule:
    name: str
    pattern: re.Pattern
    verdict: str
    kind: str = ""                    # K_DEBIT / K_CREDIT / K_TRANSFER
    reason: str = ""                  # for NOT_POSTED
    mode: Optional[TransactionMode] = None
    merchant_group: Optional[str] = None
    handle_group: Optional[str] = None
    account_group: Optional[str] = None
    date_group: Optional[str] = None
    ref_group: Optional[str] = None


def _rule(name, pattern, verdict, **kw) -> Rule:
    return Rule(name, re.compile(pattern, re.IGNORECASE | re.DOTALL),
                verdict, **kw)


# A date-bearing suffix used by card-spend rules: " on 01-JUL-2024 at ...",
# " on 2024-07-06:19:13:33."
_D = r"[^.\n]{0,120}"

RULES: List[Rule] = [
    # ---------------- NOT POSTED ----------------
    _rule("otp_kotak",
          r"\b\d{4,8}\s+is (?:the\s+)?OTP for (?:the\s+)?(?:transaction|txn)\b"
          r"|\bOTP is \d+ for\b|\bOTP for (?:the\s+)?(?:txn|transaction)\b"
          r"|\bOTP\s+for\b[\s\S]{0,30}?\bis\s+\d{4,8}\b"
          r"|\bis your OTP\b|\bOTP\s*:\s*\d{4,8}\b",
          NOT_POSTED, reason=R_OTP),
    _rule("otp_generic",
          r"\bone[\s-]?time password\b|\bOTP\b.*\bvalid (?:for|till)\b|"
          r"\bdo not share (?:the )?OTP\b|\bverification code\b|\bOTP for transaction\b",
          NOT_POSTED, reason=R_OTP),
    _rule("auth_login",
          r"\blogin\b.*\b(?:otp|code)\b|\bdo not share this (?:otp|code)\b|"
          r"\byour (?:login|verification) (?:otp|code)\b",
          NOT_POSTED, reason=R_AUTH),

    _rule("declined_card",
          r"\b(?:could not be approved|declined|was rejected|not approved)\b"
          r"|\bAuto-?Pay \(?E-?mandate\)?\s*Failed\b|\bAuto-Pay .* failed as\b"
          r"|\bhas failed\b|\bhas failed as\b|\bpayment could not be processed\b"
          r"|\btransaction failed\b|\btransaction could not\b",
          NOT_POSTED, reason=R_DECLINED),

    _rule("bill_copy_only",
          r"\bhas been sent to your registered email\b"
          r"|\bhas been sent to your email\b"
          r"|\bis available.*\bdownload\b.*\bstatement\b",
          NOT_POSTED, reason=R_STATEMENT),

    _rule("scheduled_autopay",
          r"\bis scheduled\b|\bwill be auto\s*-?\s*debited\b|\bscheduled for AutoPay\b"
          r"|\bwill be auto\b|\bhas been generated\b|\bbill .* has been raised\b"
          r"|\bamount will be debited within\b",
          NOT_POSTED, reason=R_SCHEDULED),

    # A balance alert. Real transaction SMS almost always append "Avl bal Rs. X",
    # so the phrase alone is NOT enough - the message must also contain no
    # posting verb at all.
    _rule("balance_only",
          r"^(?![\s\S]*\b(?:spent|debited|credited|received|sent|withdrawn|"
          r"transferred|refunded|charged|purchas\w*|payment\s+of|amt\s+sent|"
          r"txn|credited|received|refund)\b)"
          r"[\s\S]*\b(?:Avl\s*[Bb]al|Combined\s*[Bb]al|Avl\s+bal\s+in\s+your|"
          r"Avl\s+Bal\s+for\s+A/c|available\s+balance\s+is)\b",
          NOT_POSTED, reason=R_BALANCE),

    # A pure limit-increase alert. "available credit limit is" also appears as a
    # tail detail on real credit-card payment SMS, so this rule only fires when
    # the message contains no posting verb anywhere.
    _rule("credit_limit_alert",
          r"^(?![\s\S]*(?:credited\s+to\s+your|payment\s+of|received|spent|debited|"
          r"credited\s+to\s+the\s+card))[\s\S]*\b(?:available credit limit is|"
          r"credit limit (?:has )?increased|unused credit limit|credit limit is now)\b",
          NOT_POSTED, reason=R_BALANCE),

    _rule("statement_due",
          r"\bStatement:\s*Total due\b|\bTotal due amt\b|\bMin due amt\b"
          r"|\bPayment is due\b|\bAmt Due\b|\bTotal Due\s*:|\bMin Due\s*:"
          r"|\bDue by\s*:|\bDue Date\s*:|\bStatement is ready\b|\be-?statement\b"
          r"|\bReminder:\s*Payment for card ending\b|\bis due on\b|\bdue by\b",
          NOT_POSTED, reason=R_STATEMENT),

    # Vouchers / wallet float / gift-card credit that is not a cash credit:
    # "Rs. 430000 is ready to be used at your convenience".
    _rule("voucher_credit_notice",
          r"\bis ready to be used\b|\bis ready for use\b|\bavailable for use\b"
          r"|\bcan be used\b|\bunused balance\b|\bwallet (?:balance|amount)\b",
          NOT_POSTED, reason=R_ENVELOPE),

    # Card cashback landing as a statement credit (must sit before promo_offer,
    # which treats "Congratulations" as marketing).
    # Card reversal / refund landing on the card ("towards reversal/cashback from X").
    # Was falling through to the generic fallback and being booked as a DEBIT.
    _rule("card_reversal_credited",
          rf"{AMT}\s+(?:has been|is|was)\s+credited\s+to\s+your\s+[\w\s]{{0,30}}?card\s+(?:no\.?\s*)?[xX*]*(\d{{4}})"
          rf"[\s\S]{{0,40}}?towards\s+(?:reversal|refund|cashback)",
          POSTED, kind=K_CREDIT, mode=TransactionMode.CARD, account_group="3"),
    _rule("cashback_credited",
          rf"\bcashback of\s+{AMT}\s+(?:has been|is|was)\s+credited\b[\s\S]{{0,80}}?(\d{{4}})\b",
          POSTED, kind=K_CREDIT, account_group="3"),
    _rule("promo_offer",
          r"\bpre-?approved\b|\bloan up to\b|\beligib|\bGet \d+% off\b|\buse code\b"
          r"|\boffer ends\b|\bT&C\b|\bClaim now\b|\bclick here to know more\b"
          r"|\bLifestyle benefits\b|\bRental\b.*\bSuperfestival\b|\bReward-?Box\b"
          r"|\beligible to claim\b|\bCongratulations?\b|\bPlan expired\b"
          r"|\bzero documentation\b|\bLakh on your\b|\bfirst year\b.*\bfree\b"
          r"|\bunlimited (?:data|voice)\b|\bdata validity\b|\bGet Rs\.?\d+ voucher\b"
          r"|\bAvail\b.*\bbenefit\b|\bcashback! Get\b|\bspreading (?:festive|season) joy\b"
          r"|\bSpend \w+ & get\b|\bfor a period of\b|\bRs\.?\d+ voucher\b"
          # Telecom plan marketing: names a price but no money moves.
          r"|\bDon't miss! Recharge\b|\bUnleash the Power\b|\bPick \d+ benefits\b"
          r"|\bPostpaid Plan\b|\bRecharge Jio no\b|\bplan \([\d.]+ ?GB\b"
          r"|\badd on\b.*\bon top of\b|\bRs\.?\d+ plan\b|\bstarting (?:just )?Rs"
          # Product announcements that happen to quote a price.
          r"|\bIntroducing\b[\s\S]{0,40}\bfeature\b|\bNow make\b"
          r"|\bwe(?:'ve| have)? (?:launched|introduced)\b|\bnewly launched\b",
          NOT_POSTED, reason=R_PROMO),

    # Loan offers, telecom plan/recharge nudges, streaming promos and non-English
    # (Indic script) carrier texts. They quote a rupee figure but no money moves.
    # The leading guard keeps genuine alerts ("debited", "spent" ...) out of it.
    _rule("promo_loans_telecom_media",
          r"^(?![\s\S]*\b(?:debited|credited|spent|withdrawn|disbursed)\b)[\s\S]*?(?:"
          r"[\u0900-\u097F\u0980-\u09FF\u0A80-\u0AFF\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF\u0D00-\u0D7F]{4,}"
          r"|\b(?:personal|home|instant|urgent)\s+loans?\b"
          r"|\bloans?\s+(?:of|up to)\s+(?:Rs|INR)\b|\bGet (?:a\s+)?Loan\b|\bFIRSTmoney\b"
          r"|\bavailable for disbursal\b|\binstant funds\b|\bfunds fast\b|\bShort on funds\b"
          r"|\bLooking for a loan\b|\bApplication received\b[\s\S]{0,40}\bapplication\b"
          r"|\bWatch\b[\s\S]{0,80}\b(?:Prime Video|JioHotstar|Hotstar|Netflix|only on)\b"
          r"|\bVi (?:Max|Postpaid)\b|\bVacation Offer\b|\bExtra Data\b|\bdata quota\b"
          r"|\brecharge plan\b|\b(?:free )?plan (?:has )?expired\b|\bplan expiring\b"
          r"|\bin \d+ seconds\b|\bSet-Top Box\b|\bget unlimited\b|\bWorry-free browsing\b"
          r"|\bDO NOT SHARE!|\bOTP for additional verification\b|\bis the OTP\b|\bRESEND\b[\s\S]{0,12}\bOTP\b"
          r"|\bhealth insurance\b[\s\S]{0,60}\bcover\b|\bYour [A-Za-z]+-?\d{4} bill of\b|\bHope you love using\b"
          r")",
          NOT_POSTED, reason=R_PROMO),

    # Refund notices that only announce a refund. Money lands later and the bank
    # sends its own credit SMS, which is the one we record (on the bank's day).
    _rule("refund_pending",
          r"\brefund(?:ed|s)?\b[\s\S]{0,140}?\b(?:has been initiated|have initiated|is initiated|initiated|"
          r"will (?:be )?(?:credited|reflect|reversed|refunded|processed)|should reflect|"
          r"will get refunded|\d+(?:\s*-\s*\d+)?\s*(?:business|working)\s*days|within\s+\d+\s*(?:-\s*\d+\s*)?(?:hrs|hours|days))"
          r"|\b(?:initiated|processed)\s+(?:a\s+)?refund\b"
          r"|\bRefund (?:Update|Initiated)\b|\bwill be reversed\b",
          NOT_POSTED, reason=R_PROMO),

    # ---------------- POSTED: credit-card payment (transfer) ----------------
    _rule("cc_payment_received_hdfc",
          r"payment of\s+" + AMT + r"\s+received (?:towards|for) your credit card"
          r"[\s\S]{0,80}?(?:ending(?: with)?\s+)?(\d{4})",
          POSTED, kind=K_TRANSFER, mode=TransactionMode.CARD,
          account_group="3"),
    _rule("cc_payment_received_onedcard",
          r"payment of\s+" + AMT + r"\s+received for your credit card no\.?[\s\S]{0,60}",
          POSTED, kind=K_TRANSFER, mode=TransactionMode.CARD),
    _rule("cc_payment_credited_kotak",
          r"payment of\s+" + AMT + r"\s+is credited to your (?:kotak )?(?:bank )?"
          r"credit card\s+(?:x+)?(\d{4})",
          POSTED, kind=K_TRANSFER, mode=TransactionMode.CARD, account_group="3"),
    _rule("cc_payment_online_hdfc",
          r"Online Payment of\s+" + AMT + r"[\s\S]{0,90}?credited to your card"
          r"[\s\S]{0,30}?(?:ending\s+)?(\d{4})",
          POSTED, kind=K_TRANSFER, mode=TransactionMode.CARD, account_group="3"),
    _rule("cc_autopay_received",
          r"Payment\s+([A-Za-z0-9]+)\s+received for Auto-?Pay[\s\S]{0,60}?"
          r"(?:on|in)\s+(?:your\s+)?[\w\s]{0,20}Card\s+(?:x+)?(\d{4})",
          POSTED, kind=K_TRANSFER, mode=TransactionMode.CARD, account_group="2"),

    # ---------------- POSTED: credit (money in) ----------------
    _rule("neft_credited_beneficiary",
          rf"{AMT}\s+credited to your [\w\s/]{{0,40}}(?:a/c|account|ac)\s*"
          rf"((?:x+|\*+)\s*-?\s*\d{{4}})?[\s\S]{{0,60}}?via (?:NEFT|IMPS|RTGS)"
          rf"[\s\S]{{0,140}}?beneficiary\s+([A-Z][A-Z .]{{4,40}})"
          rf"[\s\S]{{0,60}}?(?:UTR\s*Ref\.?|Ref\.?)\s*[:#]?\s*([A-Za-z0-9]+)",
          POSTED, kind=K_CREDIT, mode=TransactionMode.NEFT,
          account_group="3", merchant_group="4", ref_group="5"),
    _rule("imps_received_linked",
          rf"received\s+{AMT}\s+on\s+{_D}?in your [\w\s/]{{0,40}}"
          rf"(?:a/c|account|ac)\s*((?:x+|\*+)\s*-?\s*\d{{4}})"
          rf"[\s\S]{{0,80}}?(?:IMPS|NEFT|RTGS)\s*(?:Ref)?\s*no\.?\s*([A-Za-z0-9]+)",
          POSTED, kind=K_CREDIT, mode=TransactionMode.IMPS,
          account_group="3", ref_group="4"),
    _rule("upi_received_handle",
          rf"received\s+{AMT}\s+in your [\w\s/]{{0,40}}(?:a/c|ac|account)\s*"
          rf"((?:x+|\*+)\s*-?\s*\d{{4}})\s+from\s+([\w.\-]+@[a-z]+)"
          rf"[\s\S]{{0,60}}?(?:UPI\s*)?Ref\s*[:#]?\s*([A-Za-z0-9]+)",
          POSTED, kind=K_CREDIT, mode=TransactionMode.UPI,
          account_group="3", handle_group="4", ref_group="5"),
    _rule("neft_credited_to_you",
          rf"{AMT}\s+has been credited to\s+([A-Za-z .]{{3,30}})\s+on\s+{_D}",
          POSTED, kind=K_CREDIT, mode=TransactionMode.NEFT, merchant_group="3"),
    _rule("sent_to_you",
          rf"has sent\s+{AMT}\s+to you[\s\S]{{0,40}}?Mode\s*[:#]?\s*(NEFT|IMPS|RTGS|IMPS)",
          POSTED, kind=K_CREDIT, mode=TransactionMode.NEFT),
    _rule("generic_credited_account",
          rf"{AMT}\s+credited\s+to\s+(?:your\s+)?(?:a/c|acct|account|acc)\b",
          POSTED, kind=K_CREDIT),
    _rule("generic_credited",
          rf"{AMT}\s+credited to your\b(?![\s\S]{{0,60}}?credit card)",
          POSTED, kind=K_CREDIT),
    _rule("generic_received",
          rf"(?:received|you have received)\s+{AMT}",
          POSTED, kind=K_CREDIT),
    _rule("refund_credited",
          rf"{AMT}\s+(?:has been\s+)?refund(?:ed)?\s+by[\s\S]{{0,80}}"
          rf"([A-Za-z][\w .&]{{2,40}})",
          POSTED, kind=K_CREDIT, merchant_group="3"),
    _rule("refund_received",
          rf"(?:received|got)\s+(?:a\s+)?refund\s+of\s+{AMT}[\s\S]{{0,60}}?"
          rf"(?:on|from)\s+(?:your\s+)?[\w\s]{{0,20}}?"
          rf"(?:from\s+)?([A-Za-z][\w .&]{{2,40}})",
          POSTED, kind=K_CREDIT, merchant_group="3"),
    _rule("generic_refunded",
          rf"{AMT}\s+refunded\b",
          POSTED, kind=K_CREDIT),

    # ---------------- POSTED: debit (money out) ----------------
    # "INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at SKOTAK-TORRENTPOWER"
    _rule("card_spend_at_merchant",
          rf"{AMT}\s+(?:is\s+)?spent\s+(?:on|from)\s+(?:your\s+)?"
          rf"([\w\s]{{0,25}}?)\s*(?:credit\s*card|card|wallet)\s*"
          rf"((?:x+|\*+)\s*-?\s*\d{{4}}|no\.?\s*(?:x+)?\d{{4}})?"
          rf"[\s\S]{{0,40}}?\bat\s+([A-Za-z0-9][\w *.&'/\-]{{2,60}}?)"
          rf"(?=\s+(?:on|for|Avl|avl|\.|$|\n))",
          POSTED, kind=K_DEBIT, mode=TransactionMode.CARD,
          account_group="4", merchant_group="5"),
    _rule("card_spend_yesbank",
          rf"{AMT}\s+spent\s+on\s+YES\s*BANK\s+Card\s+((?:x+)\s*\d{{4}})\s*@"
          rf"([A-Za-z0-9][\w *.&'/\-]{{2,40}}?)",
          POSTED, kind=K_DEBIT, mode=TransactionMode.CARD,
          account_group="3", merchant_group="4"),
    _rule("upi_txn_on_card",
          rf"Txn\s+{AMT}\s+On\s+([\w\s]{{0,25}}?)\s*Card\s+(\d{{4}})\s+At\s+"
          rf"([\w.\-]+@[a-z]+)",
          POSTED, kind=K_DEBIT, mode=TransactionMode.UPI,
          account_group="3", handle_group="4"),
    _rule("upi_amt_sent_block",
          rf"Amt Sent\s+{AMT}[\s\S]{{0,60}}?From\s+[\w\s]{{0,25}}?A/C\s*"
          rf"((?:x+|\*+)\s*-?\s*\d{{4}})[\s\S]{{0,20}}?To\s+"
          rf"([A-Za-z0-9 .*'\-]{{2,40}}?)"
          rf"(?=\s+(?:On|on|Ref|ref|Not\s+You|NOT\s+YOU|for\s+a\s+period)|$)",
          POSTED, kind=K_DEBIT, mode=TransactionMode.UPI,
          account_group="3", merchant_group="4", ref_group="5"),
    _rule("upi_sent_from_account",
          rf"Sent\s+{AMT}\s+from\s+([\w\s]{{0,25}}?)(?:A/C|AC|Acct)\s*"
          rf"((?:x+|\*+)\s*-?\s*\d{{4}})\s+to\s+([\w.\-]+@[a-z]+)"
          rf"[\s\S]{{0,80}}?(?:UPI\s*)?Ref\s*[:#]?\s*([A-Za-z0-9]+)",
          POSTED, kind=K_DEBIT, mode=TransactionMode.UPI,
          account_group="4", handle_group="5", ref_group="6"),
    _rule("debit_to_account_nach",
          rf"{AMT}\s+is debited to your Account\s+((?:x+|\*+)\s*-?\s*\d{{4}})"
          rf"[\s\S]{{0,40}}?towards\s+([A-Za-z0-9 .\-&]{{2,60}}?)\s*(?:\n|$)",
          POSTED, kind=K_DEBIT, account_group="3", merchant_group="4"),
    _rule("autopay_actual_debit",
          rf"(?:payment of|paid)\s+{AMT}[\s\S]{{0,60}}?(?:SI\s*Reference|Ref#?)\s*[:#]?\s*"
          rf"([A-Za-z0-9]+)",
          POSTED, kind=K_DEBIT, ref_group="3"),
    _rule("smartpay_debited",
          rf"(?:we have\s+)?(\S{{6,40}})\s+debited your ([\w\s]{{0,25}}?)"
          rf"credit card ending\s+(\d{{4}})[\s\S]{{0,80}}?"
          rf"(?:for the amount|amount)\s+{AMT}",
          POSTED, kind=K_DEBIT, mode=TransactionMode.CARD,
          merchant_group="1", account_group="3"),
    _rule("generic_spent",
          rf"{AMT}\s+(?:is\s+)?(?:spent|debited|charged|deducted|withdrawn)"
          rf"(?:\s+(?:on|from|at|to))?[\s\S]{{0,70}}?\b(?:at|to|on)\s+"
          rf"([A-Za-z0-9][\w *.&'/\-]{{2,50}}?)"
          rf"(?=\s+(?:on|for|avl|Avl|\.|$|\n))",
          POSTED, kind=K_DEBIT, merchant_group="3"),
    _rule("generic_debit",
          rf"{AMT}\s+(?:is\s+)?(?:debited|deducted|withdrawn|withdrawal|spent|charged)",
          POSTED, kind=K_DEBIT),
]


_CORRUPT_RULE = Rule(
    "corrupt_or_nonlatin", re.compile(re.escape("\ufffd")), NOT_POSTED,
    reason=R_ENVELOPE,
)


# ===========================================================================
# Extraction helpers
# ===========================================================================


def _to_decimal(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _amt_from_match(m: re.Match, rule: Optional[Rule] = None) -> Optional[Decimal]:
    if not m:
        return None
    # Every AMT capture is two consecutive groups: (rs-first | rs-last).
    # Skip groups the rule uses for something else (card digits, ref no. ...):
    # a 4-digit account group captured before the amount used to be returned
    # as the amount.
    skip = set()
    if rule is not None:
        for g in (rule.account_group, rule.ref_group, rule.merchant_group,
                  rule.handle_group, rule.date_group):
            if g is not None and str(g).isdigit():
                skip.add(int(g))
    for i, g in enumerate(m.groups(), start=1):
        if i in skip:
            continue
        d = _to_decimal(g)
        if d is not None:
            return d
    return None


def _grp(m: re.Match, rule: Rule, group) -> Optional[str]:
    """Pull a named or indexed capture group out of a rule match.

    `group` may be an int, a numeric string, or a group name. A numeric string
    MUST be converted to int: re treats "5" as a *named* group and raises.
    """
    if not m or group is None or group == "":
        return None
    if isinstance(group, str) and not group.isdigit():
        pass                       # a real group name
    elif isinstance(group, str):
        group = int(group)
    try:
        v = m.group(group)
    except (IndexError, KeyError):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None


def parse_date(text: str, received_at: Optional[datetime] = None) -> Optional[datetime]:
    """Try every date format seen in real bank SMS.

    Formats without a year inherit it from `received_at` (or today).
    """
    fallback = received_at or datetime.now()
    default_year = fallback.year

    for kind, rx in COMPILED_DATES:
        m = rx.search(text)
        if not m:
            continue
        g = m.groups()
        try:
            if kind == "iso":
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
                hh = int(g[3]) if len(g) > 3 and g[3] else 0
                mi = int(g[4]) if len(g) > 4 and g[4] else 0
                ss = int(g[5]) if len(g) > 5 and g[5] else 0
            elif kind == "dmy_name":
                d, mo, y = int(g[0]), _month(g[1]), int(g[2])
                hh, mi, ss = _time_from_tail(g[3])
            elif kind == "name_dmy":
                mo, d, y = _month(g[0]), int(g[1]), int(g[2])
                hh, mi, ss = _time_from_tail(g[3])
            elif kind == "dmy":
                d, mo, y = int(g[0]), int(g[1]), int(g[2])
                hh, mi, ss = _time_from_tail(g[3])
            elif kind == "dmy2":
                d, mo, yy = int(g[0]), int(g[1]), int(g[2])
                y = 2000 + yy if yy < 70 else 1900 + yy
                hh, mi, ss = _time_from_tail(g[3])
            elif kind == "dmy_guess":
                # "01-JUL" or "12/08" - the year comes from the SMS timestamp
                if g[1][:1].isalpha():
                    mo, d = _month(g[1]), int(g[0])
                else:
                    d, mo = int(g[0]), int(g[1])
                    if mo > 12:          # clearly day-month
                        d, mo = mo, d
                y = default_year
                hh = mi = ss = 0
            else:
                continue

            dt = datetime(y, mo, d, hh, mi, ss)
            # Only a *year-guessed* date can be corrected. If it lands well
            # after the SMS timestamp it almost certainly belongs to the
            # previous year ("On 28-12" arriving 02-Jan). Explicit years are
            # trusted as written.
            if kind == "dmy_guess" and dt > fallback + timedelta(days=30):
                dt = dt.replace(year=dt.year - 1)
            return dt
        except (ValueError, TypeError, IndexError):
            continue
    return None


def _time_from_tail(raw: Optional[str]) -> Tuple[int, int, int]:
    if not raw:
        return 0, 0, 0
    times = re.findall(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not times:
        return 0, 0, 0
    hh, mi, ss = times[-1]
    hour = int(hh)
    if re.search(r"[ap]\.?m", raw, re.IGNORECASE):
        if re.search(r"p", raw, re.IGNORECASE) and hour < 12:
            hour += 12
        elif re.search(r"a", raw, re.IGNORECASE) and hour == 12:
            hour = 0
    return hour, int(mi), int(ss or 0)


def normalise_last_four(raw: Optional[str]) -> Optional[str]:
    """Strip the mask from a captured account ("x4365" / "*8254" -> "4365")."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 4 else None


def extract_account(text: str) -> Optional[str]:
    """Last four digits of a masked account/card.

    Deliberately avoids 3-digit mobile masks (e.g. "mobile x130").
    """
    for rx in (ACCOUNT_CTX, ACCOUNT_MASKED, ACCOUNT_BARE):
        for m in rx.finditer(text):
            digits = normalise_last_four(m.group(1))
            if digits:
                return digits
    return None


def extract_bank(text: str) -> Optional[str]:
    low = text.lower()
    for needle, label in BANKS:
        if needle in low:
            return label
    return None


def clean_merchant(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = " ".join(value.split()).strip(" .,-–—")
    v = re.sub(r"\s+", " ", v)
    # strip aggregator prefixes repeatedly: "RAZ*MediBuddy" -> "MediBuddy"
    prev = None
    while prev != v:
        prev = v
        v = MERCHANT_JUNK.sub("", v).strip(" *.-")
    v = re.sub(r"^(?:your|the|my|our|this)\s+", "", v, flags=re.IGNORECASE)
    if len(v) < 3:
        return None
    return v[:200]


def handle_from(text: str) -> Optional[str]:
    m = UPI_HANDLE.search(text)
    return m.group(1) if m else None


def extract_ref(text: str) -> Optional[str]:
    m = UPI_REF.search(text)
    return m.group(1) if m else None


_BAL_LABEL = (r"(?:avl\.?|avail(?:able)?|avbl)\s*(?:bal(?:ance)?|lmt|limit|credit\s*limit)"
              r"|combined\s*bal|\bbal(?:ance)?\b")


def extract_balance(text: str) -> Optional[Decimal]:
    """Balance / available-limit figure printed in the message.

    Handles "Avl bal Rs.24286.41", "Avl Lmt: INR 2,84,850.00", "Avl limit INR 1234",
    and the older "balance ... is Rs X" wording.
    """
    m = (re.search(rf"(?:{_BAL_LABEL})\s*(?:is|=)?\s*[:\-]?\s*(?:rs\.?|inr|₹)\s*({NUM})",
                   text, re.IGNORECASE)
         or re.search(rf"(?:{_BAL_LABEL})[\s\S]{{0,60}}?(?:is|:)\s*(?:rs\.?|inr|₹)\s*({NUM})",
                      text, re.IGNORECASE))
    return _to_decimal(m.group(1)) if m else None


def detect_mode(text: str) -> Optional[TransactionMode]:
    for mode, rx in MODE_RULES:
        if re.search(rx, text, re.IGNORECASE):
            return mode
    return None


def classify(text: str) -> Tuple[str, Rule]:
    # Corrupted text (mangled encodings in exports) cannot be trusted: the
    # "amount" in it is usually a phone number or an offer price.
    if "\ufffd" in text:
        return NOT_POSTED, _CORRUPT_RULE
    for rule in RULES:
        if rule.pattern.search(text):
            return rule.verdict, rule
    return POSTED, Rule("generic_fallback", re.compile(""), POSTED, kind=K_DEBIT)


def split_debit_kind(text: str, handle: Optional[str],
                     merchant: Optional[str]) -> Tuple[str, Optional[str]]:
    """Decide whether a debit is really a transfer between own accounts.

    Returns (kind, reason).
    """
    h = (handle or "").lower()
    if h and CC_PAY_HANDLE.match(h):
        return K_TRANSFER, "upi handle looks like a credit-card payment"
    if h and WALLET_HANDLE.match(h):
        return K_TRANSFER, "upi handle looks like a wallet top-up"
    return K_DEBIT, ""


# ===========================================================================
# Public API
# ===========================================================================


@dataclass
class ParsedSMS:
    amount: Decimal = Decimal("0")
    txn_type: TransactionType = TransactionType.DEBIT
    txn_mode: Optional[TransactionMode] = None
    merchant: Optional[str] = None
    upi_handle: Optional[str] = None
    upi_ref: Optional[str] = None
    account_last_four: Optional[str] = None
    bank_name: Optional[str] = None
    txn_timestamp: Optional[datetime] = None
    balance: Optional[Decimal] = None

    # classification
    is_transaction: bool = False
    verdict: str = ""                  # POSTED / NOT_POSTED
    kind: str = ""                     # K_DEBIT / K_CREDIT / K_TRANSFER
    reason: Optional[str] = None       # why not a transaction
    rule: Optional[str] = None
    transfer_note: Optional[str] = None

    confidence: float = 1.0
    missing: List[str] = field(default_factory=list)

    # Backwards-compatible alias used by the categoriser
    @property
    def is_noise(self) -> bool:
        return not self.is_transaction


def parse_sms(text: str, received_at: Optional[datetime] = None) -> ParsedSMS:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    flat = " ".join(raw.split())
    out = ParsedSMS()

    if not flat.strip():
        out.reason = R_ENVELOPE
        out.verdict = NOT_POSTED
        return out

    received_at = received_at or datetime.now()

    verdict, rule = classify(flat)
    out.rule = rule.name

    # ---- not a transaction -------------------------------------------------
    if verdict == NOT_POSTED:
        out.is_transaction = False
        out.verdict = NOT_POSTED
        out.reason = rule.reason or R_ENVELOPE
        out.balance = extract_balance(flat)
        # still capture what we can, for context in the review UI
        out.account_last_four = extract_account(flat)
        out.bank_name = extract_bank(flat)
        out.confidence = 1.0
        return out

    # ---- posted: extract fields ------------------------------------------
    m = rule.pattern.search(flat)
    out.is_transaction = True
    out.verdict = POSTED

    if m:
        out.amount = _amt_from_match(m, rule)
    if out.amount is None:
        # Fall back to the first currency-tagged number in the message.
        out.amount = _amt_from_match(re.search(AMT, flat, re.IGNORECASE)) or Decimal("0")

    out.account_last_four = (normalise_last_four(_grp(m, rule, rule.account_group))
                             if rule else None) or extract_account(flat)
    out.merchant = clean_merchant(_grp(m, rule, rule.merchant_group)) if rule else None

    # Always try to pick up a UPI handle, even when we already have a merchant
    # name ("zomato" plus the handle "zomato@paytm" are both useful).
    handle = _grp(m, rule, rule.handle_group) if rule else None
    out.upi_handle = handle or handle_from(flat)

    # Rule did not give us a merchant: harvest it generically, then fall back
    # to the UPI handle (paying "zomato@paytm" tells us more than nothing).
    if not out.merchant:
        out.merchant = _harvest_merchant(flat)
    if not out.merchant and out.upi_handle:
        out.merchant = clean_merchant(out.upi_handle.split("@")[0])
    if out.merchant and _looks_like_handle(out.merchant) and not out.upi_handle:
        out.upi_handle = out.merchant
    out.upi_ref = (_grp(m, rule, rule.ref_group) if rule else None) or extract_ref(flat)
    out.txn_timestamp = parse_date(flat, received_at) or received_at
    out.bank_name = extract_bank(flat)
    out.balance = extract_balance(flat)
    out.txn_mode = rule.mode if (rule and rule.mode) else detect_mode(flat)

    # ---- direction ---------------------------------------------------------
    kind = rule.kind or K_DEBIT
    if kind == K_DEBIT:
        kind, note = split_debit_kind(flat, out.upi_handle, out.merchant)
        out.transfer_note = note
    out.kind = kind
    out.txn_type = TransactionType.CREDIT if kind == K_CREDIT else TransactionType.DEBIT

    # ---- confidence --------------------------------------------------------
    score = 1.0
    if out.amount <= 0:
        score -= 0.5
    if not out.merchant:
        score -= 0.15
    if not out.account_last_four:
        score -= 0.1
    if rule.name == "generic_fallback":
        score -= 0.2
    out.confidence = max(0.0, round(score, 2))
    if out.amount <= 0:
        out.missing.append("amount")
    if rule.name == "generic_fallback":
        out.verdict, out.reason, out.is_transaction = REVIEW, "unknown_format", False
    return out


def _harvest_merchant(text: str) -> Optional[str]:
    """Last-resort merchant extraction for long-tail messages."""
    for rx in (MERCHANT_VERB, MERCHANT_AT, MERCHANT_FOR):
        m = rx.search(text)
        if not m:
            continue
        val = clean_merchant(m.group(1))
        if val and "@" in val:
            return clean_merchant(val.split("@")[0])
        if val:
            return val
    return None


def _is_generic(rule: Rule) -> bool:
    return rule.name in {
        "generic_credited", "generic_received", "generic_spent", "generic_debit",
        "generic_refunded", "generic_fallback",
    }


def _looks_like_handle(value: Optional[str]) -> bool:
    return bool(value and "@" in value and "." in value)