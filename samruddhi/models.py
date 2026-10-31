"""Data model. All money is stored as integer paise (1 rupee = 100) so sums are exact."""
from datetime import date, datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

ACCOUNT_KINDS = {
    "bank": "Bank account",
    "credit_card": "Credit card",
    "debit_card": "Debit card",
    "upi": "UPI",
    "wallet": "Wallet / prepaid",
    "investment": "Investment",
    "loan": "Loan",
    "cash": "Cash",
}
LINKED_KINDS = ("debit_card", "upi")            # no balance of their own - hit a bank account
SPENDING_KINDS = ("bank", "credit_card", "debit_card", "upi", "wallet", "cash")
GOAL_KINDS = ("bank", "investment")             # goals can sit only here
BANK_SUBTYPES = ["Savings", "Current", "Salary", "Minor", "Joint", "Other"]
RECURRENCES = {"none": "Does not repeat", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}
PERIODS = {"weekly": "Weekly", "monthly": "Monthly", "yearly": "Annual"}
TXN_TYPES = ("income", "expense", "transfer")


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Household(db.Model):
    __tablename__ = "households"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="HOUSEHOLD # 1")
    created_at = db.Column(db.DateTime, default=now)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=True)
    email = db.Column(db.String(160), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_minor = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    theme = db.Column(db.String(30), default="daylight")
    glass = db.Column(db.Boolean, default=False, nullable=False, server_default=db.false())
    created_at = db.Column(db.DateTime, default=now)

    household = db.relationship("Household")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)

    @property
    def role(self):
        return "admin" if self.is_admin else "member"

    @property
    def can_login(self):
        """Minors are tags only: they never sign in."""
        return bool(self.active and not self.is_minor and self.password_hash and self.username)


class Account(db.Model):
    __tablename__ = "accounts"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    institution = db.Column(db.String(120), nullable=False, default="")     # bank / company, used for grouping
    kind = db.Column(db.String(20), nullable=False)
    subtype = db.Column(db.String(40), default="")
    last4 = db.Column(db.String(4), default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)   # NULL = shared
    purpose = db.Column(db.String(60), default="")
    # Cards/loans: negative = amount owed. Everything else: positive = money held.
    starting_balance = db.Column(db.BigInteger, default=0, nullable=False)
    credit_limit = db.Column(db.BigInteger, default=0)
    due_day = db.Column(db.Integer, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)   # add-on card -> parent card
    linked_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)   # debit card / UPI -> bank
    pay_from_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)  # default payer for reminders
    current_value = db.Column(db.BigInteger, nullable=True)       # investments, manual
    loan_total = db.Column(db.BigInteger, default=0)              # original loan amount
    emi_principal = db.Column(db.BigInteger, default=0)
    emi_interest = db.Column(db.BigInteger, default=0)
    upi_id = db.Column(db.String(80), nullable=True)               # UPI accounts: the handle, e.g. name@bank
    archived = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=now)

    owner = db.relationship("User", foreign_keys=[owner_id])      # primary holder (kept in sync with holders)
    holders = db.relationship("AccountHolder", cascade="all, delete-orphan", backref="account",
                              order_by="AccountHolder.id")
    parent = db.relationship("Account", foreign_keys=[parent_id], remote_side=[id])
    linked = db.relationship("Account", foreign_keys=[linked_id], remote_side=[id])
    pay_from = db.relationship("Account", foreign_keys=[pay_from_id], remote_side=[id])

    @property
    def kind_label(self):
        return ACCOUNT_KINDS.get(self.kind, self.kind)

    @property
    def has_own_balance(self):
        return self.kind not in LINKED_KINDS

    @property
    def primary_holder(self):
        for h in self.holders:
            if h.role == "primary":
                return h.user
        return self.owner

    @property
    def secondary_holders(self):
        return [h.user for h in self.holders if h.role == "secondary"]

    @property
    def holder_ids(self):
        p = self.primary_holder
        return ([p.id] if p else []) + [u.id for u in self.secondary_holders]

    @property
    def group_name(self):
        """Debit cards / UPI sit under the bank they draw from."""
        if self.kind in LINKED_KINDS and self.linked and self.linked.institution:
            return self.linked.institution
        return self.institution or "Other"

    @property
    def display(self):
        tail = f" ••{self.last4}" if self.last4 else ""
        return f"{self.name}{tail}"


class AccountHolder(db.Model):
    """Who holds an account. One primary; any number of secondary (joint holders, a minor's guardian)."""
    __tablename__ = "account_holders"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(10), default="primary", nullable=False)     # primary | secondary
    user = db.relationship("User")


class UpiApp(db.Model):
    """Editable master list of UPI payment apps (Paytm, GPay, PhonePe, Cred...). Archived, never deleted."""
    __tablename__ = "upi_apps"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    archived = db.Column(db.Boolean, default=False, nullable=False)


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(10), nullable=False)        # income | expense | transfer
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    system_key = db.Column(db.String(30), nullable=True)   # cashback, cc_payment, loan_emi, loan_interest...
    start_date = db.Column(db.Date, nullable=True)         # optional time-bound window
    end_date = db.Column(db.Date, nullable=True)
    archived = db.Column(db.Boolean, default=False, nullable=False)

    parent = db.relationship("Category", remote_side=[id], backref="children")

    @property
    def path(self):
        return f"{self.parent.name} › {self.name}" if self.parent else self.name

    def active_on(self, d: date) -> bool:
        if self.archived:
            return False
        if self.start_date and d < self.start_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        return True


class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    type = db.Column(db.String(10), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.BigInteger, nullable=False)       # always positive
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    to_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    note = db.Column(db.String(500), default="")
    status = db.Column(db.String(10), default="posted", nullable=False, index=True)   # posted|pending|rejected
    source = db.Column(db.String(15), default="manual", nullable=False)               # manual|subscription|cashback|reminder
    scope = db.Column(db.String(12), default="family", nullable=False)                # family|individuals
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=now)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    # subscriptions
    recurrence = db.Column(db.String(10), default="none", nullable=False)
    next_run = db.Column(db.Date, nullable=True)
    rec_active = db.Column(db.Boolean, default=True, nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True)
    # cashback + grouping
    rule_id = db.Column(db.Integer, db.ForeignKey("cashback_rules.id"), nullable=True)
    source_txn_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True)
    group_id = db.Column(db.String(40), nullable=True, index=True)
    upi_app_id = db.Column(db.Integer, db.ForeignKey("upi_apps.id"), nullable=True)   # payment app, UPI accounts only

    upi_app = db.relationship("UpiApp")
    account = db.relationship("Account", foreign_keys=[account_id])
    to_account = db.relationship("Account", foreign_keys=[to_account_id])
    category = db.relationship("Category")
    creator = db.relationship("User", foreign_keys=[created_by])
    beneficiaries = db.relationship("TxnBeneficiary", cascade="all, delete-orphan", backref="txn")

    __table_args__ = (UniqueConstraint("template_id", "date", name="uq_sub_instance"),)

    @property
    def is_template(self):
        return self.recurrence != "none" and self.template_id is None

    @property
    def beneficiary_ids(self):
        return sorted(b.user_id for b in self.beneficiaries)


class TxnBeneficiary(db.Model):
    __tablename__ = "txn_beneficiaries"
    id = db.Column(db.Integer, primary_key=True)
    txn_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    share = db.Column(db.BigInteger, nullable=True)      # custom amount; NULL = equal split
    user = db.relationship("User")


class CashbackRule(db.Model):
    __tablename__ = "cashback_rules"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)       # purchases made with this
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    keyword = db.Column(db.String(80), default="")                                         # matched in the note
    mode = db.Column(db.String(10), default="percent", nullable=False)                     # percent | fixed
    basis_points = db.Column(db.Integer, default=0)                                        # 500 = 5%
    fixed_amount = db.Column(db.BigInteger, default=0)
    min_spend = db.Column(db.BigInteger, default=0)
    max_cashback = db.Column(db.BigInteger, default=0)                                     # 0 = no cap
    delay_days = db.Column(db.Integer, default=0)
    payout_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)  # NULL = same account
    one_time = db.Column(db.Boolean, default=False)
    uses = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True, nullable=False)

    account = db.relationship("Account", foreign_keys=[account_id])
    payout_account = db.relationship("Account", foreign_keys=[payout_account_id])
    category = db.relationship("Category")


class Budget(db.Model):
    __tablename__ = "budgets"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)   # NULL = family
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    period = db.Column(db.String(10), nullable=False)
    amount = db.Column(db.BigInteger, nullable=False)
    archived = db.Column(db.Boolean, default=False, nullable=False)

    owner = db.relationship("User")
    category = db.relationship("Category")


class Goal(db.Model):
    __tablename__ = "goals"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)   # NULL = shared
    target = db.Column(db.BigInteger, default=0)
    deadline = db.Column(db.Date, nullable=True)
    archived = db.Column(db.Boolean, default=False, nullable=False)

    owner = db.relationship("User")
    entries = db.relationship("GoalEntry", backref="goal", cascade="all, delete-orphan")


class GoalEntry(db.Model):
    """Earmarks money for a goal. Never moves money between accounts."""
    __tablename__ = "goal_entries"
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    amount = db.Column(db.BigInteger, nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), default="posted", nullable=False)
    source = db.Column(db.String(10), default="manual")      # manual | txn | rule
    txn_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("goal_rules.id"), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    note = db.Column(db.String(300), default="")

    account = db.relationship("Account")


class GoalRule(db.Model):
    """Recurring assignment - each run creates a pending entry that needs approval."""
    __tablename__ = "goal_rules"
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    amount = db.Column(db.BigInteger, nullable=False)
    recurrence = db.Column(db.String(10), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    next_run = db.Column(db.Date, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    goal = db.relationship("Goal")
    account = db.relationship("Account")


class Reminder(db.Model):
    """Card bill / EMI due. Approving it generates the transfer."""
    __tablename__ = "reminders"
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.BigInteger, nullable=False)
    status = db.Column(db.String(10), default="pending", nullable=False)   # pending | done | dismissed
    account = db.relationship("Account")

    __table_args__ = (UniqueConstraint("account_id", "due_date", name="uq_reminder"),)


# order matters for CSV backups and for deletes (children first)
TRANSACTIONAL_MODELS = [GoalEntry, GoalRule, TxnBeneficiary, Reminder]
