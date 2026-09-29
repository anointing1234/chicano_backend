"""Write an AuditLog row for every staff action (call from staff views/services)."""
from apps.core.models import AuditLog


def log_action(request, action: str, target, data: dict | None = None) -> AuditLog:
    """
    log_action(request, "provider.approve", provider, {"note": "..."})
    `target` is any model instance; its class name and pk are stored.
    """
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip() or None
    return AuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target.__class__.__name__,
        target_id=str(target.pk),
        data=data or {},
        ip_address=ip,
    )
