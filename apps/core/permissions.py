"""
Who may call what.

    IsCustomer          any active user with a customer profile (both User apps)
    IsProvider          user with a provider profile (Driver app or Rider app), any status
    IsApprovedProvider  provider whose status is APPROVED (needed to go online / take trips)
    IsStaff             Super Admin staff account
    HasStaffRole(...)   staff with one of the given roles (SUPER_ADMIN always passes)

Suspended or banned users are rejected by every permission here with 403
`account_suspended`, so the apps can show the "account on hold" screen.
"""
from rest_framework.permissions import BasePermission

from apps.core.exceptions import ApiError


def _active_or_raise(user):
    if user and user.is_authenticated and getattr(user, "status", "active") != "active":
        raise ApiError("account_suspended", "Your account is on hold. Contact support.", status_code=403,
                       details={"status": user.status})


class IsCustomer(BasePermission):
    message = "This endpoint is for customer accounts."

    def has_permission(self, request, view):
        _active_or_raise(request.user)
        return bool(request.user and request.user.is_authenticated and hasattr(request.user, "customer_profile"))


class IsProvider(BasePermission):
    message = "This endpoint is for driver and rider accounts."

    def has_permission(self, request, view):
        _active_or_raise(request.user)
        return bool(request.user and request.user.is_authenticated and hasattr(request.user, "provider_profile"))


class IsApprovedProvider(IsProvider):
    message = "Your driver/rider account isn't approved yet."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        profile = request.user.provider_profile
        if profile.status != "approved":
            raise ApiError("provider_not_approved", self.message, status_code=403, details={"status": profile.status})
        return True


class IsStaff(BasePermission):
    message = "Staff only."

    def has_permission(self, request, view):
        _active_or_raise(request.user)
        return bool(request.user and request.user.is_authenticated and request.user.is_staff and request.user.staff_role)


def HasStaffRole(*roles: str):
    """
    Factory: `permission_classes = [HasStaffRole("finance")]`.
    SUPER_ADMIN can do everything.
    """
    class _HasStaffRole(IsStaff):
        message = f"Requires one of these staff roles: {', '.join(roles)}."

        def has_permission(self, request, view):
            if not super().has_permission(request, view):
                return False
            return request.user.staff_role == "super_admin" or request.user.staff_role in roles

    _HasStaffRole.__name__ = f"HasStaffRole_{'_'.join(roles)}"
    return _HasStaffRole


def StaffArea(area: str):
    """
    Factory: `permission_classes = [StaffArea("payouts")]`.
    Reads the shared role matrix in apps/core/roles.py (the web dashboard uses the same one).
    """
    from apps.core.roles import has_area

    class _StaffArea(IsStaff):
        message = f"Your staff role can't use '{area}'."

        def has_permission(self, request, view):
            return super().has_permission(request, view) and has_area(request.user, area)

    _StaffArea.__name__ = f"StaffArea_{area.replace('.', '_')}"
    return _StaffArea
