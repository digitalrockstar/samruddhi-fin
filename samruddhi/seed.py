"""Starter categories for a new household. Admin can add/archive more later."""
from .models import Category, UpiApp, db

DEFAULTS = {
    "expense": {
        "Food": ["Dining out", "Food delivery", "Snacks"],
        "Grocery": ["Zepto", "Blinkit", "Local store", "Vegetables"],
        "Cab": ["Uber", "Ola", "Rapido"],
        "Fuel": ["Petrol", "Diesel", "CNG"],
        "Shopping": ["Amazon", "Flipkart", "Clothing", "Electronics"],
        "Bills": ["Electricity", "Internet", "Mobile", "Water", "Gas"],
        "Home": ["Rent", "Maintenance", "Help & services"],
        "Health": ["Doctor", "Pharmacy", "Insurance"],
        "Education": ["Fees", "Books", "Courses"],
        "Entertainment": ["Subscriptions", "Movies", "Travel & trips"],
        "Kids": ["School", "Toys", "Activities"],
        "Finance charges": ["Loan interest", "Card fees", "Taxes"],
        "Other": ["Miscellaneous"],
    },
    "income": {
        "Income": ["Salary", "Business", "Cricket", "Interest", "Gifts"],
        "Cashback": ["Cashback", "Rewards"],
    },
    "transfer": {
        "Transfer": ["CC Payment", "Loan EMI", "Investment", "Self transfer", "Wallet top-up"],
    },
}
SYSTEM_KEYS = {("income", "Cashback"): "cashback", ("transfer", "CC Payment"): "cc_payment",
               ("transfer", "Loan EMI"): "loan_emi", ("expense", "Loan interest"): "loan_interest"}


def seed_categories(household_id):
    for kind, tree in DEFAULTS.items():
        for l1_name, kids in tree.items():
            l1 = Category(household_id=household_id, name=l1_name, kind=kind)
            db.session.add(l1)
            db.session.flush()
            for k in kids:
                db.session.add(Category(household_id=household_id, name=k, kind=kind, parent_id=l1.id,
                                        system_key=SYSTEM_KEYS.get((kind, k))))
    db.session.flush()


UPI_APPS = ["Paytm", "GPay", "PhonePe", "Cred"]


def seed_upi_apps(household_id):
    """Starter payment apps. Only when the household has never had any (archived ones count)."""
    if UpiApp.query.filter_by(household_id=household_id).count():
        return
    for n in UPI_APPS:
        db.session.add(UpiApp(household_id=household_id, name=n))
    db.session.flush()
