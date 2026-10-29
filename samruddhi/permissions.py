"""Role-based access, kept in one table so roles can be added or changed without touching routes.
Add a role: put it in ROLES with its permissions, and (if needed) map a User field to it in User.role."""
from functools import wraps

from flask import abort, g, redirect, request, url_for

ROLES = {
    "admin": {"*"},
    "member": {"view", "txn.add", "txn.edit_own", "budget.own", "goal.own", "goal.contribute"},
}
PERMISSIONS = {
    "view": "See all household data",
    "txn.add": "Add transactions",
    "txn.edit_own": "Edit own transactions",
    "txn.edit_any": "Edit anyone's transactions",
    "accounts.manage": "Add, edit, archive accounts",
    "cashback.manage": "Manage cashback rules",
    "budget.family": "Create family budgets",
    "budget.own": "Create own budgets",
    "goal.shared": "Create shared goals",
    "goal.own": "Create own goals",
    "goal.contribute": "Add money to any visible goal",
    "approvals.manage": "Approve or reject auto-created items",
    "categories.manage": "Create or archive categories",
    "people.manage": "Manage members and roles",
    "data.reset": "Backup and reset data",
}


def can(user, perm) -> bool:
    if not user:
        return False
    granted = ROLES.get(user.role, set())
    return "*" in granted or perm in granted


def require(perm):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not g.user:
                return redirect(url_for("auth.login", next=request.path))
            if not can(g.user, perm):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco
