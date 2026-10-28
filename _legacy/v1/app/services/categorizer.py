"""Auto-categorisation: learned merchant rules first, then keyword heuristics."""
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, MerchantMapping, RecurringTransaction, Transaction
from app.schemas import CategoryType, TransactionType

# keyword -> (root type, suggested leaf name)
KEYWORD_RULES: List[Tuple[str, str, str]] = [
    # Food & Dining
    ("swiggy", "expense", "Food Delivery"),
    ("zomato", "expense", "Food Delivery"),
    ("blinkit", "expense", "Groceries"),
    ("zepto", "expense", "Groceries"),
    ("bigbasket", "expense", "Groceries"),
    ("grofers", "expense", "Groceries"),
    ("dmart", "expense", "Groceries"),
    ("reliance fresh", "expense", "Groceries"),
    ("dominos", "expense", "Restaurants"),
    ("pizza hut", "expense", "Restaurants"),
    ("mcdonald", "expense", "Restaurants"),
    ("kfc", "expense", "Restaurants"),
    ("starbucks", "expense", "Coffee/Snacks"),
    ("café", "expense", "Coffee/Snacks"),
    ("cafe", "expense", "Coffee/Snacks"),
    ("third wave", "expense", "Coffee/Snacks"),
    # Utilities / bills seen in real statements
    ("torrent power", "expense", "Electricity"),
    ("torrent power-lightbil", "expense", "Electricity"),
    ("torrent power-na", "expense", "Electricity"),
    ("bses", "expense", "Electricity"),
    ("adani electricity", "expense", "Electricity"),
    ("tata power", "expense", "Electricity"),
    ("cesc", "expense", "Electricity"),
    ("mseb", "expense", "Electricity"),
    ("gail gas", "expense", "Gas"),
    ("gujarat gas", "expense", "Gas"),
    ("indraprastha gas", "expense", "Gas"),
    ("mahanagar gas", "expense", "Gas"),
    ("banagas", "expense", "Gas"),
    ("airtel", "expense", "Telecom"),
    ("airtel xstream", "expense", "Internet"),
    ("vi postpaid", "expense", "Telecom"),
    ("vodafone", "expense", "Telecom"),
    ("jio", "expense", "Telecom"),
    ("hathway", "expense", "Internet"),
    ("act fibernet", "expense", "Internet"),
    ("jio fiber", "expense", "Internet"),
    ("bbps", "expense", "Bills"),
    ("bill payment", "expense", "Bills"),
    ("smartpay", "expense", "Bills"),
    # Insurance seen in real data
    ("icici lombard", "expense", "Insurance"),
    ("hdfc ergo", "expense", "Insurance"),
    ("bajaj allianz", "expense", "Insurance"),
    ("policybazaar", "expense", "Insurance"),
    # Meal / gift vouchers
    ("pluxee", "expense", "Meal Voucher"),
    ("sodexo", "expense", "Meal Voucher"),
    ("meal wallet", "expense", "Meal Voucher"),
    # Education / kids
    ("school", "expense", "School/Fees"),
    ("vidyalaya", "expense", "School/Fees"),
    ("tuition", "expense", "Tuition/Fees"),
    ("udemy", "expense", "Tuition/Fees"),
    ("coursera", "expense", "Tuition/Fees"),
    ("lenskart", "expense", "Eyewear"),
    ("apollo", "expense", "Pharmacy/Medical"),
    ("medplus", "expense", "Pharmacy/Medical"),
    ("pharmeasy", "expense", "Pharmacy/Medical"),
    ("1mg", "expense", "Pharmacy/Medical"),
    ("tirupati", "expense", "Travel"),
    # Fuel / vehicles
    ("petrol", "expense", "Fuel"),
    ("diesel", "expense", "Fuel"),
    ("hpcl", "expense", "Fuel"),
    ("bpcl", "expense", "Fuel"),
    ("iocl", "expense", "Fuel"),
    ("indian oil", "expense", "Fuel"),
    ("bharat petroleum", "expense", "Fuel"),
    ("hp petrol", "expense", "Fuel"),
    ("petrol pump", "expense", "Fuel"),
    ("mp fuels", "expense", "Fuel"),
    # Subscriptions seen in real data
    ("netflix", "expense", "Streaming"),
    ("spotify", "expense", "Streaming"),
    ("hotstar", "expense", "Streaming"),
    ("prime video", "expense", "Streaming"),
    ("youtube", "expense", "Streaming"),
    ("adobe", "expense", "Subscriptions"),
    ("microsoft 365", "expense", "Subscriptions"),
    ("linkedin", "expense", "Subscriptions"),
    ("chatgpt", "expense", "Subscriptions"),
    ("openai", "expense", "Subscriptions"),
    ("canva", "expense", "Subscriptions"),
    ("zoom", "expense", "Subscriptions"),
    # Food / grocery seen in real data
    ("reliance fresh", "expense", "Groceries"),
    ("dmart", "expense", "Groceries"),
    ("grofers", "expense", "Groceries"),
    ("blinkit", "expense", "Groceries"),
    ("zepto", "expense", "Groceries"),
    ("bigbasket", "expense", "Groceries"),
    ("swiggy", "expense", "Food Delivery"),
    ("zomato", "expense", "Food Delivery"),
    ("mustard", "expense", "Restaurants"),
    ("third wave", "expense", "Coffee/Snacks"),
    ("starbucks", "expense", "Coffee/Snacks"),
    ("barista", "expense", "Coffee/Snacks"),
    # Shopping
    ("amazon", "expense", "Amazon"),
    ("flipkart", "expense", "Online Shopping"),
    ("myntra", "expense", "Clothing"),
    ("shopify", "expense", "Online Shopping"),
    ("lens", "expense", "Eyewear"),
    ("ikea", "expense", "Home & Furnishing"),
    ("wakefit", "expense", "Home & Furnishing"),
    ("decathlon", "expense", "Sports"),
    # Travel
    ("irctc", "expense", "Rail/Train"),
    ("indigo", "expense", "Flights"),
    ("spicejet", "expense", "Flights"),
    ("air india", "expense", "Flights"),
    ("akasa", "expense", "Flights"),
    ("easyjet", "expense", "Flights"),
    ("makemytrip", "expense", "Travel Booking"),
    ("goibibo", "expense", "Travel Booking"),
    ("ixigo", "expense", "Travel Booking"),
    ("oyo", "expense", "Hotels"),
    ("taj", "expense", "Hotels"),
    ("adani lounge", "expense", "Lounge/Access"),
    # Transfers
    ("credit card payment", "transfer", "Credit Card Payment"),
    ("cc payment", "transfer", "Credit Card Payment"),
    ("loan emi", "transfer", "Loan Repayment"),
    ("emi paid", "transfer", "Loan Repayment"),
    ("home loan", "transfer", "Loan Repayment"),
    ("car loan", "transfer", "Loan Repayment"),
    ("mutual fund", "transfer", "Investment Funding"),
    ("sip purchase", "transfer", "Investment Funding"),
    ("flexi cap", "transfer", "Investment Funding"),
    ("hdfc flexi", "transfer", "Investment Funding"),
    ("investor", "transfer", "Investment Funding"),
    ("nach", "transfer", "Investment Funding"),
    # Income
    ("salary", "income", "Salary"),
    ("payroll", "income", "Salary"),
    ("wages", "income", "Salary"),
    ("dividend", "income", "Interest/Dividends"),
    ("interest", "income", "Interest/Dividends"),
    ("refund", "income", "Refunds"),
    ("cashback", "income", "Cashback Received"),
    ("reward", "income", "Cashback Received"),
    # Wallet / prepaid instruments
    ("wallet", "transfer", "Wallet Top-up"),
    ("gpay-", "transfer", "Wallet Top-up"),
    ("mygate", "transfer", "Wallet Top-up"),
    ("phonepe", "transfer", "Wallet Top-up"),
    # Home & living
    ("rent", "expense", "Rent"),
    ("house rent", "expense", "Rent"),
    ("society maintenance", "expense", "Society/Maintenance"),
    ("maintenance charge", "expense", "Society/Maintenance"),
    ("municipal tax", "expense", "Taxes"),
    ("property tax", "expense", "Taxes"),
    ("income tax", "expense", "Taxes"),
    ("advance tax", "expense", "Taxes"),
    ("tds", "expense", "Taxes"),
    ("tcs", "expense", "Taxes"),
    ("gst payment", "expense", "Taxes"),
    ("salon", "expense", "Salon/Beauty"),
    ("barber", "expense", "Salon/Beauty"),
    ("gym", "expense", "Fitness"),
    ("charity", "expense", "Donations"),
    ("donation", "expense", "Donations"),
    # Cash
    ("atm", "expense", "Cash & ATM"),
    ("cash withdrawal", "expense", "Cash & ATM"),
    # Transport
    ("uber", "expense", "Cab/Ride Share"),
    ("ola", "expense", "Cab/Ride Share"),
    ("rapido", "expense", "Cab/Ride Share"),
    ("metro", "expense", "Public Transport"),
    ("parking", "expense", "Parking/Tolls"),
    ("fastag", "expense", "Parking/Tolls"),
    # Health
    ("pharmacy", "expense", "Pharmacy/Medical"),
    ("fortis", "expense", "Hospital"),
    ("max healthcare", "expense", "Hospital"),
    ("practo", "expense", "Doctor/Consult"),
    ("dental", "expense", "Doctor/Consult"),
    # Home & living
    ("rent", "expense", "Rent"),
    ("house rent", "expense", "Rent"),
    ("society maintenance", "expense", "Society/Maintenance"),
    ("maintenance charge", "expense", "Society/Maintenance"),
    ("municipal tax", "expense", "Taxes"),
    ("property tax", "expense", "Taxes"),
    ("income tax", "expense", "Taxes"),
    ("advance tax", "expense", "Taxes"),
    ("tds", "expense", "Taxes"),
    ("tuition", "expense", "Tuition/Fees"),
    ("school fee", "expense", "Tuition/Fees"),
    ("udemy", "expense", "Tuition/Fees"),
    # Cash
    ("atm", "expense", "Cash & ATM"),
    ("cash withdrawal", "expense", "Cash & ATM"),
    ("withdrawn from atm", "expense", "Cash & ATM"),
    ("salon", "expense", "Salon/Beauty"),
    ("barber", "expense", "Salon/Beauty"),
    ("gym", "expense", "Fitness"),
    ("charity", "expense", "Donations"),
    ("donation", "expense", "Donations"),
    # Transport
    ("uber", "expense", "Cab/Ride Share"),
    ("ola", "expense", "Cab/Ride Share"),
    ("rapido", "expense", "Cab/Ride Share"),
    ("indigo", "expense", "Flights"),
    ("spicejet", "expense", "Flights"),
    ("air india", "expense", "Flights"),
    ("akasa", "expense", "Flights"),
    ("irctc", "expense", "Rail/Train"),
    ("railway", "expense", "Rail/Train"),
    ("metro", "expense", "Public Transport"),
    ("petrol", "expense", "Fuel"),
    ("diesel", "expense", "Fuel"),
    ("hpcl", "expense", "Fuel"),
    ("bpcl", "expense", "Fuel"),
    ("iocl", "expense", "Fuel"),
    ("makemytrip", "expense", "Travel Booking"),
    ("goibibo", "expense", "Travel Booking"),
    ("ixigo", "expense", "Travel Booking"),
    ("booking.com", "expense", "Travel Booking"),
    ("airbnb", "expense", "Travel Booking"),
    # Shopping
    ("amazon", "expense", "Amazon"),
    ("flipkart", "expense", "Online Shopping"),
    ("myntra", "expense", "Clothing"),
    ("ajio", "expense", "Clothing"),
    ("nykaa", "expense", "Personal Care"),
    ("meesho", "expense", "Online Shopping"),
    # Bills
    ("electricity", "expense", "Electricity"),
    ("adani electricity", "expense", "Electricity"),
    ("tata power", "expense", "Electricity"),
    ("bsnl", "expense", "Telecom"),
    ("airtel", "expense", "Telecom"),
    ("jio", "expense", "Telecom"),
    ("recharge", "expense", "Mobile Recharge"),
    ("broadband", "expense", "Internet"),
    ("hathway", "expense", "Internet"),
    ("act fibernet", "expense", "Internet"),
    ("insurance", "expense", "Insurance"),
    ("netflix", "expense", "Streaming"),
    ("spotify", "expense", "Streaming"),
    ("hotstar", "expense", "Streaming"),
    ("prime video", "expense", "Streaming"),
    ("youtube premium", "expense", "Streaming"),
    # Health
    ("apollo", "expense", "Pharmacy/Medical"),
    ("fortis", "expense", "Hospital"),
    ("max healthcare", "expense", "Hospital"),
    ("pharmacy", "expense", "Pharmacy/Medical"),
    ("medplus", "expense", "Pharmacy/Medical"),
    ("1mg", "expense", "Pharmacy/Medical"),
    ("pharmeasy", "expense", "Pharmacy/Medical"),
    ("practo", "expense", "Doctor/Consult"),
    # Income
    ("salary", "income", "Salary"),
    ("payroll", "income", "Salary"),
    ("wages", "income", "Salary"),
    ("dividend", "income", "Interest/Dividends"),
    ("interest", "income", "Interest/Dividends"),
    ("refund", "income", "Refunds"),
    ("cashback", "income", "Cashback Received"),
    ("rewards", "income", "Cashback Received"),
    ("rental income", "income", "Other Income"),
    # Transfers
    ("credit card payment", "transfer", "Credit Card Payment"),
    ("cc payment", "transfer", "Credit Card Payment"),
    ("loan emi", "transfer", "Loan Repayment"),
    ("emi paid", "transfer", "Loan Repayment"),
    ("home loan", "transfer", "Loan Repayment"),
    ("mutual fund", "transfer", "Investment Funding"),
    ("sip", "transfer", "Investment Funding"),
]

# Normalise the literal type strings to enums once, at import
KEYWORD_RULES: List[Tuple[str, CategoryType, str]] = [
    (kw, CategoryType(t), leaf) for kw, t, leaf in KEYWORD_RULES
]

# Longer, more specific phrases must be checked before shorter ones
KEYWORD_RULES.sort(key=lambda r: -len(r[0]))


def _contains_phrase(haystack: str, keyword: str) -> bool:
    """Word-boundary phrase match.

    Plain substring matching is actively dangerous here: "rent" is inside
    "torrent", so "TORRENTPOWER" (their electricity provider) was being
    categorised as Rent. Every keyword is matched as whole words instead.
    """
    if keyword in _ALWAYS_SUBSTRING:
        return keyword in haystack
    pattern = r"(?<![0-9a-z])" + re.escape(keyword) + r"(?![0-9a-z])"
    return re.search(pattern, haystack) is not None


# A few phrases are legitimately part of a larger token.
_ALWAYS_SUBSTRING = {"gpay-", "cheq.payu", "cheq1", "hdfc flexi", "flexi cap"}


class Categorizer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def categorize(self, text: str, merchant: Optional[str], txn_type: TransactionType) -> Optional[int]:
        """Return a category_id, or None if we need a human."""
        learned = await self.match_learned(merchant)
        if learned:
            return learned

        # Keywords: merchant first (more specific), then the whole message so
        # that "credited ... towards salary" still resolves when the parser
        # only found a bank name.
        if merchant:
            guessed = await self.match_keywords(merchant)
            if guessed:
                return guessed

        guessed = await self.match_keywords(text)
        if guessed:
            return guessed
        return None

    async def spam_category(self) -> Optional[int]:
        return await self._category_id(CategoryType.IGNORE, "Spam")

    async def match_learned(self, merchant: Optional[str]) -> Optional[int]:
        if not merchant:
            return None

        rows = (
            await self.db.execute(
                select(MerchantMapping)
                .where(MerchantMapping.is_active.is_(True))
                .order_by(MerchantMapping.confidence.desc())
            )
        ).scalars().all()

        needle = merchant.lower().strip()
        for m in rows:
            if m.pattern.lower() == needle:
                await self._touch(m.id)
                return m.category_id

        # Partial: mapping pattern contained in the merchant name
        for m in rows:
            p = m.pattern.lower()
            if len(p) >= 4 and p in needle:
                await self._touch(m.id)
                return m.category_id

        # Partial: merchant name contained in the mapping pattern
        for m in rows:
            p = m.pattern.lower()
            if len(p) >= 4 and needle in p and len(needle) >= 4:
                await self._touch(m.id)
                return m.category_id
        return None

    async def match_keywords(self, text: str) -> Optional[int]:
        low = (text or "").lower()
        if not low:
            return None

        for keyword, cat_type, leaf in KEYWORD_RULES:
            if not _contains_phrase(low, keyword):
                continue
            cid = await self._category_id(cat_type, leaf, create_if_missing=True)
            if cid:
                return cid
        return None

    async def _touch(self, mapping_id: int) -> None:
        await self.db.execute(
            update(MerchantMapping)
            .where(MerchantMapping.id == mapping_id)
            .values(last_used_at=datetime.now())
        )

    async def _category_id(
        self, root_type: CategoryType, name: str, create_if_missing: bool = False
    ) -> Optional[int]:
        rows = (
            await self.db.execute(
                select(Category).where(Category.type == root_type, Category.parent_id.is_(None))
            )
        ).scalars().all()
        root = next((r for r in rows if r.name.upper() == root_type.value.upper()), None)
        if not root and rows:
            root = rows[0]
        if not root:
            return None

        found = (
            await self.db.execute(
                select(Category).where(Category.parent_id == root.id, Category.name == name)
            )
        ).scalar_one_or_none()
        if found:
            return found.id
        if not create_if_missing:
            return None

        new_cat = Category(
            name=name, type=root_type, parent_id=root.id,
            is_system=False, display_order=999,
        )
        self.db.add(new_cat)
        await self.db.flush()
        return new_cat.id

    async def learn(self, merchant: str, category_id: int, confidence: int = 100) -> None:
        """Create/update a mapping so future hits auto-categorise."""
        if not merchant:
            return
        pattern = merchant.strip()[:200]
        existing = (
            await self.db.execute(
                select(MerchantMapping).where(MerchantMapping.pattern == pattern)
            )
        ).scalar_one_or_none()
        if existing:
            existing.category_id = category_id
            existing.confidence = confidence
            existing.is_active = True
        else:
            self.db.add(MerchantMapping(
                pattern=pattern, category_id=category_id, confidence=confidence
            ))

    async def detect_recurring(self, txn: Transaction, lookback_days: int = 400) -> Optional[RecurringTransaction]:
        """Flag a transaction when the same merchant+amount repeats monthly."""
        from datetime import timedelta

        if not txn.merchant or txn.is_spam or not txn.txn_timestamp:
            return None
        if txn.category_id is None:
            return None

        cat = await self.db.get(Category, txn.category_id)
        if not cat or cat.type not in (CategoryType.EXPENSE, CategoryType.INCOME):
            return None

        window_start = txn.txn_timestamp - timedelta(days=lookback_days)
        rows = (
            await self.db.execute(
                select(Transaction).where(
                    Transaction.merchant.ilike(txn.merchant),
                    Transaction.parsed_amount == txn.parsed_amount,
                    Transaction.txn_type == txn.txn_type,
                    Transaction.id != txn.id,
                    Transaction.is_spam.is_(False),
                    Transaction.txn_timestamp >= window_start,
                ).order_by(Transaction.txn_timestamp.desc()).limit(12)
            )
        ).scalars().all()

        if len(rows) < 2:
            return None

        dates = sorted([r.txn_timestamp for r in rows] + [txn.txn_timestamp])
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = sum(gaps) / len(gaps)

        frequency = None
        confidence = 60
        next_gap = 30
        if 25 <= avg_gap <= 35:
            frequency, next_gap, confidence = "monthly", 30, 85
        elif 80 <= avg_gap <= 100:
            frequency, next_gap, confidence = "quarterly", 90, 80
        elif 350 <= avg_gap <= 380:
            frequency, next_gap, confidence = "yearly", 365, 75
        if not frequency:
            return None

        existing = (
            await self.db.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.merchant.ilike(txn.merchant),
                    RecurringTransaction.amount == txn.parsed_amount,
                    RecurringTransaction.frequency == frequency,
                )
            )
        ).scalar_one_or_none()

        next_date = txn.txn_timestamp + timedelta(days=next_gap)
        if existing:
            existing.transaction_id = txn.id
            existing.next_expected_date = next_date
            existing.last_detected_at = datetime.now()
            existing.confidence = min(existing.confidence + 5, 99)
            existing.is_active = True
            return existing

        rec = RecurringTransaction(
            transaction_id=txn.id,
            merchant=txn.merchant[:200],
            amount=txn.parsed_amount,
            frequency=frequency,
            next_expected_date=next_date,
            last_detected_at=datetime.now(),
            confidence=confidence,
        )
        self.db.add(rec)
        await self.db.flush()
        return rec