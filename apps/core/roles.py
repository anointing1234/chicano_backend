"""
Staff role matrix: the ONE place that says which staff role may do what.

Used by:
  * the Super Admin web dashboard (apps/dashboard, Django templates) via `@staff_area("...")`
  * the staff JSON API (apps/staff, /api/v1/staff/) via `StaffArea("...")`

Roles (accounts.StaffRole): super_admin, operations, support, compliance, finance.
`super_admin` may do everything. An empty list means "any staff member".
"""
SUPER_ADMIN_ONLY: list[str] = ["__super_admin_only__"]   # no real role has this name

STAFF_AREAS: dict[str, list[str]] = {
    # dashboard home + live map
    "overview": [],
    # the combined users table (customers, drivers, riders, staff)
    "users.view": ["operations", "support"],
    "users.change": ["operations"],                       # suspend / ban / reinstate
    # drivers (cars) and riders (bikes)
    "providers.view": ["operations", "compliance", "support"],
    "providers.approve": ["compliance"],                  # approve/reject applications, documents, vehicles
    "providers.suspend": ["operations", "compliance"],
    # trips
    "rides.view": ["operations", "support", "finance"],
    "rides.refund": ["finance", "support"],               # support is capped, see SUPPORT_REFUND_LIMIT
    "rides.cancel": ["operations"],
    "dispatch": ["operations"],
    # money
    "payouts": ["finance"],
    "growth": ["operations", "finance"],                  # promo codes + incentives
    "payments.view": ["finance", "support", "operations"],  # payment records, refunds history, fare disputes
    "reports": ["operations", "finance"],                  # analytics + CSV exports
    # support desk + safety
    "support": ["support", "operations"],                  # tickets, lost items, SOS
    "broadcasts": ["operations"],                         # push + inbox message to a whole audience
    # configuration
    "settings.view": [],                                  # ride types, fares, zones (read)
    "settings.edit": SUPER_ADMIN_ONLY,
    "team": SUPER_ADMIN_ONLY,
    "audit": SUPER_ADMIN_ONLY,
}

# Plain-language descriptions shown on Settings › Staff & roles (keep in sync with STAFF_AREAS).
AREA_LABELS: dict[str, str] = {
    "overview": "See the dashboards and live map",
    "users.view": "Look up customers, drivers, riders and their history",
    "users.change": "Suspend, ban or reinstate any account",
    "providers.view": "See driver and rider profiles, vehicles and documents",
    "providers.approve": "Review documents and approve or reject drivers, riders and vehicles",
    "providers.suspend": "Suspend or reinstate drivers and riders",
    "rides.view": "See trips, timelines and fares",
    "rides.refund": "Refund trips (support: up to ₦5,000 each)",
    "rides.cancel": "Cancel trips that haven't started",
    "dispatch": "Assign waiting rides to a driver or rider by hand",
    "payouts": "Run payouts and mark them paid or failed",
    "growth": "Create and edit promo codes and incentives",
    "payments.view": "See payment records and refund history",
    "reports": "Open reports and export CSV files",
    "support": "Answer tickets, handle lost items and SOS alerts",
    "broadcasts": "Send a message to a whole group of users",
    "settings.view": "See fares, ride types and service zones",
    "settings.edit": "Change fares, ride types and service zones",
    "team": "Add staff and change their roles",
    "audit": "Read the audit log",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "super_admin": "Everything, including prices, staff accounts and the audit log.",
    "operations": "Runs the day: dispatch, trips, accounts, promotions, broadcasts.",
    "support": "Customer care: tickets, lost items, SOS and small refunds.",
    "compliance": "Onboarding: documents, vehicles and driver/rider approval.",
    "finance": "Money: payouts, refunds, payments and reports.",
}

SUPPORT_REFUND_LIMIT = 500_000   # kobo (₦5,000): the most a support agent may refund in one go


def is_staff_member(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff and user.staff_role and user.status == "active")


def has_area(user, area: str) -> bool:
    """True if this staff user may use `area` (see STAFF_AREAS)."""
    if not is_staff_member(user):
        return False
    if user.staff_role == "super_admin":
        return True
    allowed = STAFF_AREAS[area]
    return not allowed or user.staff_role in allowed


def role_matrix() -> list[dict]:
    """Rows for the roles page: one per area with which roles have it."""
    roles = list(ROLE_DESCRIPTIONS)
    rows = []
    for area, allowed in STAFF_AREAS.items():
        rows.append({"area": area, "label": AREA_LABELS.get(area, area),
                     "cells": [r == "super_admin" or not allowed or r in allowed for r in roles]})   # same order as ROLE_DESCRIPTIONS
    return rows
