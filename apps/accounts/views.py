"""
Auth endpoints used by all four mobile apps (and staff login for the Super Admin).

Mobile login flow (sign-up and login are the same flow):

    1. POST /api/v1/auth/otp/request/  {phone}                 -> SMS sent
    2. POST /api/v1/auth/otp/verify/   {phone, code, app}      -> {access, refresh, is_new_user, user}
    3. (is_new_user) PATCH /api/v1/auth/me/ {first_name, last_name, email}
    4. POST /api/v1/auth/devices/      {app, platform, push_token}   -> push notifications
    ... on 401: POST /api/v1/auth/token/refresh/ {refresh} -> new pair, retry the request once.
    Logout: POST /api/v1/auth/logout/ {refresh}
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import ApiError
from apps.core.schema import errors

from . import services
from .models import AppKind, Device
from .serializers import (AuthResponseSerializer, DeviceSerializer, OtpRequestResponseSerializer, OtpRequestSerializer,
                          OtpVerifySerializer, RefreshSerializer, StaffAuthResponseSerializer, StaffLoginSerializer,
                          StaffUserSerializer, TokenPairSerializer, UserSerializer, UserUpdateSerializer)

User = get_user_model()


class OtpRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "otp"   # 5 requests/minute per client (settings.REST_FRAMEWORK)

    @extend_schema(
        tags=["Auth"],
        summary="Send a login code by SMS",
        description=(
            "**POST /api/v1/auth/otp/request/** · No auth.\n\n"
            "Sends a 6-digit code to the phone. Works for new and existing users (sign-up = login). "
            "Show a resend button after `resend_after_seconds`. In development the response also "
            "contains `debug_code` so you can log in without SMS."
        ),
        request=OtpRequestSerializer,
        responses={200: OtpRequestResponseSerializer, **errors(400, 429)},
        examples=[OpenApiExample("Request", value={"phone": "0803 412 5567"}, request_only=True),
                  OpenApiExample("Response", value={"phone": "+2348034125567", "expires_in_seconds": 300,
                                                    "resend_after_seconds": 30, "debug_code": "482917"}, response_only=True)],
    )
    def post(self, request):
        import os
        import secrets
        from datetime import timedelta

        from django.conf import settings

        from .models import OTPCode, OtpPurpose

        data = OtpRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone = services.normalize_phone(data.validated_data["phone"])

        # TEMPORARY (until the Termii sender ID is approved): send the code back in the response
        # so the app can log in. Turn it off on Render with OTP_RETURN_CODE_IN_RESPONSE=false.
        # While this is on, anyone who knows a phone number can log in as that user.
        return_code = settings.OTP_DEBUG_RETURN_CODE or os.environ.get(
            "OTP_RETURN_CODE_IN_RESPONSE", "true").strip().lower() in ("1", "true", "yes", "on")

        # Same steps as services.issue_otp: invalidate older codes, save a new hashed code, send it.
        code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
        with transaction.atomic():
            OTPCode.objects.filter(phone=phone, purpose=OtpPurpose.LOGIN, consumed_at__isnull=True).update(
                consumed_at=timezone.now())
            OTPCode.objects.create(
                phone=phone,
                purpose=OtpPurpose.LOGIN,
                code_hash=services._hash_code(phone, code),
                expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS),
            )
        try:
            services.send_sms(phone, f"Your Chicano Cruise code is {code}. "
                                     f"It expires in {settings.OTP_TTL_SECONDS // 60} minutes.")
        except ApiError as exc:
            # If the SMS fails but the code is being returned, the app can still log in.
            if not (return_code and exc.error_code == "sms_failed"):
                raise

        body = {"phone": phone, "expires_in_seconds": settings.OTP_TTL_SECONDS, "resend_after_seconds": 30}
        if return_code:
            body["debug_code"] = code
        return Response(body)


class OtpVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "otp"

    @extend_schema(
        tags=["Auth"],
        summary="Verify the code and get tokens",
        description=(
            "**POST /api/v1/auth/otp/verify/** · No auth.\n\n"
            "Returns a JWT pair and the user. Creates the account on first login.\n\n"
            "* `app=user_cars|user_bikes`: a customer profile is created automatically.\n"
            "* `app=driver_cars|rider_bikes`: if `user.roles` has no `driver`/`rider`, show onboarding "
            "and call `POST /api/v1/provider/apply/`.\n\n"
            "Error codes: `otp_invalid` (details.attempts_remaining), `otp_expired`, `otp_locked`, "
            "`account_suspended` (403)."
        ),
        request=OtpVerifySerializer,
        responses={200: AuthResponseSerializer, **errors(400, 403, 429)},
        examples=[OpenApiExample("Request", value={"phone": "+2348034125567", "code": "482917", "app": "user_cars"}, request_only=True)],
    )
    def post(self, request):
        data = OtpVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone = services.normalize_phone(data.validated_data["phone"])
        services.verify_otp(phone, data.validated_data["code"])

        with transaction.atomic():
            user, created = services.get_or_create_user_for_phone(phone)
            if user.status != "active":
                raise ApiError("account_suspended", "Your account is on hold. Contact support.", status_code=403,
                               details={"status": user.status, "reason": user.status_reason})
            if data.validated_data["app"] in (AppKind.USER_CARS, AppKind.USER_BIKES):
                from apps.customers.services import ensure_customer_profile   # local import: avoid app cycles
                ensure_customer_profile(user)
            user.last_login = timezone.now()
            user.save(update_fields=["last_login", "updated_at"])
            user.refresh_from_db()

        return Response({**services.tokens_for(user), "is_new_user": created or not user.first_name,
                         "user": UserSerializer(user, context={"request": request}).data})


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Auth"],
        summary="Get a new access token",
        description=(
            "**POST /api/v1/auth/token/refresh/** · No auth header; send the refresh token in the body.\n\n"
            "Returns a new access token AND a new refresh token (rotation). Always store the new refresh "
            "token; the old one is blacklisted. On 401 `token_not_valid`, send the user to login."
        ),
        request=RefreshSerializer,
        responses={200: TokenPairSerializer, **errors(400, 401)},
    )
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise ApiError("token_not_valid", "Please log in again.", status_code=401) from exc
        return Response(serializer.validated_data)


class LogoutView(APIView):
    @extend_schema(
        tags=["Auth"],
        summary="Log out (revoke the refresh token)",
        description="**POST /api/v1/auth/logout/** · Bearer token required. Also delete the device's push token with DELETE /auth/devices/{id}/.",
        request=RefreshSerializer,
        responses={204: None, **errors(400, 401)},
    )
    def post(self, request):
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except TokenError:
            pass   # already invalid: logging out is idempotent
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Auth"], summary="Get the logged-in user",
                   description="**GET /api/v1/auth/me/** · Bearer token required. Use `roles` to decide which screens to show.",
                   responses={200: UserSerializer, **errors(401)})
    def get(self, request):
        return Response(UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(tags=["Auth"], summary="Update name, email or photo",
                   description="**PATCH /api/v1/auth/me/** · Bearer token required. JSON, or multipart/form-data when sending `photo`.",
                   request=UserUpdateSerializer, responses={200: UserSerializer, **errors(400, 401)})
    def patch(self, request):
        serializer = UserUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(tags=["Auth"], summary="Delete my account",
                   description=("**DELETE /api/v1/auth/me/** · Bearer token required. Deactivates the account and removes "
                                "personal details. Trip and payment records are kept for legal reasons."),
                   responses={204: None, **errors(401, 409)})
    def delete(self, request):
        # Local import avoids a circular import (rides imports accounts).
        from apps.rides.models import ACTIVE_STATUSES, Ride
        user = request.user
        # Ride.customer is the User itself (combined users table), so filter on the user directly.
        if Ride.objects.filter(customer=user, status__in=ACTIVE_STATUSES).exists():
            raise ApiError("active_ride", "Finish or cancel your current ride first.", status_code=409)
        user.first_name, user.last_name, user.email = "Deleted", "User", None
        user.phone = f"deleted-{user.id.hex[:12]}"
        user.is_active = False
        user.photo = None
        user.save()
        user.devices.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceListCreateView(APIView):
    @extend_schema(tags=["Auth"], summary="Register this phone for push notifications",
                   description=("**POST /api/v1/auth/devices/** · Bearer token required. Call after login and whenever "
                                "the Expo push token changes. Upserts by `push_token`."),
                   request=DeviceSerializer, responses={201: DeviceSerializer, **errors(400, 401)})
    def post(self, request):
        serializer = DeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, _ = Device.objects.update_or_create(
            push_token=serializer.validated_data["push_token"],
            defaults={**serializer.validated_data, "user": request.user},
        )
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED)


class DeviceDeleteView(APIView):
    @extend_schema(tags=["Auth"], summary="Stop push notifications for a device",
                   description="**DELETE /api/v1/auth/devices/{id}/** · Bearer token required. Call on logout.",
                   responses={204: None, **errors(401, 404)})
    def delete(self, request, pk):
        deleted, _ = Device.objects.filter(pk=pk, user=request.user).delete()
        if not deleted:
            raise ApiError("not_found", "We couldn't find that.", status_code=404)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StaffLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "otp"

    @extend_schema(
        tags=["Staff"],
        summary="Staff login (Super Admin web app)",
        description=("**POST /api/v1/staff/auth/login/** · No auth. Email + password for staff accounts only. "
                     "Returns the same JWT pair format as mobile login."),
        request=StaffLoginSerializer,
        responses={200: StaffAuthResponseSerializer, **errors(400, 403, 429)},
    )
    def post(self, request):
        data = StaffLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=data.validated_data["email"], is_staff=True).first()
        if not user or not user.check_password(data.validated_data["password"]) or not user.staff_role:
            raise ApiError("invalid_credentials", "Email or password is incorrect.", status_code=400)
        if user.status != "active" or not user.is_active:
            raise ApiError("account_suspended", "This staff account is disabled.", status_code=403)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login", "updated_at"])
        return Response({**services.tokens_for(user), "user": StaffUserSerializer(user).data})