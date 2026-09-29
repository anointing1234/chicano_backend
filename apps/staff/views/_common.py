"""
Shared bits for every staff API view.

Who may call what is defined ONCE in apps/core/roles.py (STAFF_AREAS) and shared with the
Super Admin web dashboard. The permission classes below are just named handles for it.
"""
from drf_spectacular.utils import OpenApiParameter
from rest_framework.permissions import SAFE_METHODS

from apps.core.exceptions import ApiError
from apps.core.permissions import IsStaff, StaffArea
from apps.core.roles import SUPPORT_REFUND_LIMIT  # noqa: F401  (re-exported for views)

TAG = "Staff"

# --- permission classes, one per area of apps/core/roles.py ------------------------------
AnyStaff = IsStaff
UsersRead = StaffArea("users.view")
UsersWrite = StaffArea("users.change")
ProvidersRead = StaffArea("providers.view")
ComplianceOnly = StaffArea("providers.approve")
SuspendRoles = StaffArea("providers.suspend")
OperationsOnly = StaffArea("dispatch")
RidesRead = StaffArea("rides.view")
RefundRoles = StaffArea("rides.refund")
CancelRoles = StaffArea("rides.cancel")
FinanceOnly = StaffArea("payouts")
GrowthRoles = StaffArea("growth")
SupportRoles = StaffArea("support")
SuperAdminOnly = StaffArea("settings.edit")
TeamRoles = StaffArea("team")
AuditRoles = StaffArea("audit")


class ReadVsWritePermissionMixin:
    """
    For viewsets where reading and writing need different roles.
    Set `read_permission` and `write_permission` on the class.
    """
    read_permission = IsStaff
    write_permission = SuperAdminOnly

    def get_permissions(self):
        cls = self.read_permission if self.request.method in SAFE_METHODS else self.write_permission
        return [cls()]


# --- the ?service= filter used across the dashboard ---------------------------------------
SERVICE_PARAM = OpenApiParameter(
    "service", str, enum=["all", "car", "bike"], default="all",
    description="Filter by business line: car (drivers / User app · Cars) or bike (riders / User app · Bikes).",
)


def service_filter(request) -> str | None:
    """Read ?service=all|car|bike. Returns None for 'all'. Raises a 400 for anything else."""
    value = (request.query_params.get("service") or "all").lower()
    if value == "all":
        return None
    if value not in ("car", "bike"):
        raise ApiError("validation_error", "service must be all, car or bike.", details={"service": ["Invalid value."]})
    return value
