"""
One error format for the entire API.

Every non-2xx response looks like this, so the mobile apps need exactly one error parser:

    {
      "error": {
        "code": "validation_error",          # stable, machine-readable; switch on this
        "message": "Check the highlighted fields.",   # safe to show to the user
        "details": {"phone": ["Enter a valid Nigerian phone number."]}   # optional
      }
    }

Codes used across the API:
    validation_error (400)      invalid body or query params; `details` maps field -> messages
    not_authenticated (401)     missing/expired access token -> refresh, then retry once
    token_not_valid (401)       token invalid or blacklisted -> send the user to login
    permission_denied (403)     authenticated but not allowed (e.g. customer calling staff API)
    not_found (404)
    conflict (409)              action not allowed in the current state (e.g. cancel a completed ride)
    throttled (429)             too many requests; `details.retry_after_seconds`
    <domain codes>              raised with `ApiError`, e.g. "otp_invalid", "quote_expired",
                                "helmet_check_required", "no_active_ride"
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler


class ApiError(exceptions.APIException):
    """
    Raise this from services/views for business-rule failures.

        raise ApiError("quote_expired", "This fare has expired. Get a new estimate.", status_code=409)
    """
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, message: str, status_code: int | None = None, details: dict | None = None):
        super().__init__(detail=message, code=code)
        if status_code:
            self.status_code = status_code
        self.error_code = code
        self.extra_details = details or {}


class Conflict(ApiError):
    """409: the request is valid but the record is in the wrong state."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(code, message, status.HTTP_409_CONFLICT, details)


# Friendly default messages for DRF's built-in exceptions.
_DEFAULT_MESSAGES = {
    "not_authenticated": "Please log in again.",
    "authentication_failed": "Please log in again.",
    "permission_denied": "You don't have access to this.",
    "not_found": "We couldn't find that.",
    "method_not_allowed": "This action isn't supported here.",
    "throttled": "Too many attempts. Please wait a moment and try again.",
    "parse_error": "The request body isn't valid JSON.",
    "unsupported_media_type": "Send JSON (or multipart for file uploads).",
}


def api_exception_handler(exc, context):
    """DRF EXCEPTION_HANDLER: wrap every error in the `{"error": {...}}` envelope."""
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_default_handler(exc, context)
    if response is None:
        # Unhandled server error: let Django return a 500 (and log it). Never leak internals.
        return None

    if isinstance(exc, ApiError):
        body = {"code": exc.error_code, "message": str(exc.detail), "details": exc.extra_details}
    elif isinstance(exc, exceptions.ValidationError):
        details = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        body = {"code": "validation_error", "message": "Check the highlighted fields.", "details": details}
    else:
        codes = exc.get_codes() if hasattr(exc, "get_codes") else "error"
        code = codes if isinstance(codes, str) else "error"
        # SimpleJWT puts its code in detail["code"] ("token_not_valid").
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            code = str(exc.detail["code"])
        message = _DEFAULT_MESSAGES.get(code) or (str(exc.detail) if not isinstance(exc.detail, dict) else "Request failed.")
        details = {}
        if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
            details["retry_after_seconds"] = int(exc.wait)
        body = {"code": code, "message": message, "details": details}

    # Keep only the headers clients need (auth challenge, retry hint).
    keep = {k: v for k, v in response.items() if k in ("WWW-Authenticate", "Retry-After")}
    return Response({"error": body}, status=response.status_code, headers=keep)
