from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from enum import Enum


class AccountType(str, Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    SAVINGS = "savings"
    CURRENT = "current"
    UPI = "upi"
    WALLET = "wallet"


class CashbackType(str, Enum):
    CASH = "cash"
    POINTS = "points"
    WALLET = "wallet"


class CategoryType(str, Enum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    IGNORE = "ignore"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionMode(str, Enum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    ATM = "atm"
    WALLET = "wallet"
    CASH = "cash"
    IMPS = "imps"
    NEFT = "neft"
    RTGS = "rtgs"


class CashbackStatus(str, Enum):
    PENDING = "pending"
    RECEIVED = "received"
    EXPIRED = "expired"


class AllocationType(str, Enum):
    EXPENSE_FOR = "expense_for"
    INCOME_FROM = "income_from"
    PAID_BY = "paid_by"


# Person
class PersonBase(BaseModel):
    name: str
    prefix: str
    display_order: int = 0


class PersonCreate(PersonBase):
    pass


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    prefix: Optional[str] = None
    display_order: Optional[int] = None


class PersonResponse(PersonBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Account
class AccountBase(BaseModel):
    bank_name: str
    account_type: AccountType
    last_four: Optional[str] = None
    upi_id: Optional[str] = None
    nickname: Optional[str] = None
    default_cashback_pct: Decimal = Decimal("0")
    default_cashback_type: Optional[CashbackType] = None
    default_cashback_wallet: Optional[str] = None
    is_active: bool = True


class AccountCreate(AccountBase):
    person_id: int


class AccountUpdate(BaseModel):
    bank_name: Optional[str] = None
    account_type: Optional[AccountType] = None
    last_four: Optional[str] = None
    upi_id: Optional[str] = None
    nickname: Optional[str] = None
    default_cashback_pct: Optional[Decimal] = None
    default_cashback_type: Optional[CashbackType] = None
    default_cashback_wallet: Optional[str] = None
    is_active: Optional[bool] = None


class AccountResponse(AccountBase):
    id: int
    person_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Category
class CategoryBase(BaseModel):
    name: str
    type: CategoryType
    parent_id: Optional[int] = None
    icon: Optional[str] = None
    is_system: bool = False
    display_order: int = 0


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[CategoryType] = None
    parent_id: Optional[int] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None


class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime
    children: List["CategoryResponse"] = []

    model_config = ConfigDict(from_attributes=True)


CategoryResponse.model_rebuild()


# Transaction
class TransactionAllocationBase(BaseModel):
    person_id: Optional[int] = None
    amount: Decimal
    allocation_type: AllocationType
    notes: Optional[str] = None


class TransactionAllocationCreate(TransactionAllocationBase):
    pass


class TransactionAllocationResponse(TransactionAllocationBase):
    id: int
    transaction_id: int
    created_at: datetime
    person_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionBase(BaseModel):
    person_id: int
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    raw_text: str
    parsed_amount: Decimal
    currency: str = "INR"
    txn_type: TransactionType
    txn_mode: Optional[TransactionMode] = None
    merchant: Optional[str] = None
    upi_ref: Optional[str] = None
    txn_timestamp: datetime
    cashback_amount: Decimal = Decimal("0")
    cashback_type: Optional[CashbackType] = None
    cashback_wallet: Optional[str] = None
    cashback_status: CashbackStatus = CashbackStatus.PENDING
    is_spam: bool = False
    needs_review: bool = False
    notes: Optional[str] = None


class TransactionCreate(TransactionBase):
    message_id: Optional[int] = None
    allocations: List[TransactionAllocationCreate] = []


class TransactionUpdate(BaseModel):
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    parsed_amount: Optional[Decimal] = None
    txn_type: Optional[TransactionType] = None
    txn_mode: Optional[TransactionMode] = None
    merchant: Optional[str] = None
    cashback_amount: Optional[Decimal] = None
    cashback_type: Optional[CashbackType] = None
    cashback_wallet: Optional[str] = None
    cashback_status: Optional[CashbackStatus] = None
    is_spam: Optional[bool] = None
    needs_review: Optional[bool] = None
    notes: Optional[str] = None
    allocations: Optional[List[TransactionAllocationCreate]] = None


class TransactionResponse(TransactionBase):
    id: int
    message_id: Optional[int] = None
    received_at: datetime
    is_split_parent: bool
    parent_txn_id: Optional[int] = None
    created_at: datetime
    person_name: str
    account_nickname: Optional[str] = None
    category_name: Optional[str] = None
    category_type: Optional[CategoryType] = None
    allocations: List[TransactionAllocationResponse] = []

    model_config = ConfigDict(from_attributes=True)


# Filters
class TransactionFilters(BaseModel):
    person_id: Optional[int] = None
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    txn_type: Optional[TransactionType] = None
    txn_mode: Optional[TransactionMode] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    merchant: Optional[str] = None
    needs_review: Optional[bool] = None
    is_spam: Optional[bool] = None
    page: int = 1
    page_size: int = 50


# Dashboard
class DashboardSummary(BaseModel):
    total_expense: Decimal
    total_income: Decimal
    net: Decimal
    transaction_count: int = 0
    by_category: List[dict]
    by_mode: List[dict]
    by_person: List[dict]
    daily_trend: List[dict]
    weekly_trend: List[dict]
    monthly_trend: List[dict]


class CategoryBreakdown(BaseModel):
    category_id: int
    category_name: str
    parent_category: Optional[str] = None
    total_amount: Decimal
    transaction_count: int
    percentage: float


class PersonSummary(BaseModel):
    person_id: int
    person_name: str
    paid_amount: Decimal
    expense_for_amount: Decimal
    income_from_amount: Decimal
    net_amount: Decimal


# Merchant Mapping
class MerchantMappingBase(BaseModel):
    pattern: str
    category_id: int
    confidence: int = 100
    is_active: bool = True


class MerchantMappingCreate(MerchantMappingBase):
    pass


class MerchantMappingResponse(MerchantMappingBase):
    id: int
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Recurring
class RecurringTransactionResponse(BaseModel):
    id: int
    merchant: str
    amount: Decimal
    frequency: str
    next_expected_date: datetime
    is_active: bool
    confidence: int
    created_at: datetime
    last_detected_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Pagination
class PaginatedResponse(BaseModel):
    items: List[TransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int