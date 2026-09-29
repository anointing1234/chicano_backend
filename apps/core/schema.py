"""
OpenAPI helpers so every endpoint documents its error responses the same way.

Usage in a view:

    @extend_schema(
        summary="Cancel a ride",
        responses={200: RideSerializer, **errors(400, 401, 404, 409)},
    )

`errors()` expands to documented responses that all use `ErrorResponse` below, so the
Swagger UI shows the exact error body the app will receive.
"""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Stable machine-readable code, e.g. `validation_error`, `quote_expired`.")
    message = serializers.CharField(help_text="Human-readable message, safe to show in the app.")
    details = serializers.DictField(help_text="Extra data. For validation errors: field name -> list of messages.")


class ErrorResponseSerializer(serializers.Serializer):
    """The single error envelope used by every endpoint."""
    error = ErrorBodySerializer()


_DESCRIPTIONS = {
    400: ("Validation or business-rule error", {"code": "validation_error", "message": "Check the highlighted fields.", "details": {"phone": ["Enter a valid Nigerian phone number."]}}),
    401: ("Missing, expired or invalid access token. Refresh the token and retry once; if that fails, log in again.", {"code": "not_authenticated", "message": "Please log in again.", "details": {}}),
    402: ("Payment problem: card declined or wallet balance too low", {"code": "insufficient_wallet", "message": "Your wallet balance is too low.", "details": {"balance_amount": 120000, "required_amount": 706500}}),
    403: ("Authenticated but not allowed (wrong role, suspended account, or not your record)", {"code": "permission_denied", "message": "You don't have access to this.", "details": {}}),
    404: ("Record not found (or not visible to you)", {"code": "not_found", "message": "We couldn't find that.", "details": {}}),
    409: ("Action not allowed in the record's current state", {"code": "invalid_state", "message": "This ride can no longer be cancelled.", "details": {"status": "completed"}}),
    429: ("Too many requests", {"code": "throttled", "message": "Too many attempts. Please wait a moment and try again.", "details": {"retry_after_seconds": 42}}),
}


def errors(*status_codes: int) -> dict:
    """Build the `responses` entries for the given error status codes."""
    out = {}
    for code in status_codes:
        description, example = _DESCRIPTIONS[code]
        out[code] = OpenApiResponse(
            response=ErrorResponseSerializer,
            description=description,
            examples=[OpenApiExample(f"{code} example", value={"error": example}, response_only=True, status_codes=[str(code)])],
        )
    return out


# Common reusable tiny response shapes
MessageSerializer = inline_serializer("Message", {"detail": serializers.CharField()})
