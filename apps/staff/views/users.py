"""
Super Admin > Users. One list over the COMBINED users table: customers, drivers, riders
and staff all live in accounts.User, so this is where you find anyone by phone.

    GET  /api/v1/staff/users/?role=&status=&search=&page=
    GET  /api/v1/staff/users/{id}/
    POST /api/v1/staff/users/{id}/suspend/     {reason}
    POST /api/v1/staff/users/{id}/ban/         {reason}
    POST /api/v1/staff/users/{id}/reinstate/

Suspending/banning takes effect on the person's next request (every permission class
checks `User.status` and answers 403 `account_suspended`), in every app they use.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserStatus
from apps.core.schema import errors

from .. import services
from ..serializers import ReasonSerializer, StaffUserDetailSerializer, StaffUserRowSerializer
from ._common import TAG, UsersRead, UsersWrite

User = get_user_model()


class UserListView(generics.ListAPIView):
    permission_classes = [UsersRead]
    serializer_class = StaffUserRowSerializer
    filter_backends = []     # filters are applied by hand below (and documented as parameters)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        qs = User.objects.select_related("customer_profile", "provider_profile").order_by("-date_joined")
        p = self.request.query_params
        role = p.get("role")
        # Roles come from which profile rows exist (see accounts.User.roles).
        if role == "customer":
            qs = qs.filter(customer_profile__isnull=False)
        elif role == "driver":
            qs = qs.filter(provider_profile__service="car")
        elif role == "rider":
            qs = qs.filter(provider_profile__service="bike")
        elif role == "staff":
            qs = qs.filter(is_staff=True)
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("search"):
            s = p["search"].strip()
            qs = qs.filter(Q(phone__icontains=s.lstrip("0")) | Q(first_name__icontains=s) | Q(last_name__icontains=s) | Q(email__icontains=s))
        return qs

    @extend_schema(
        tags=[TAG], summary="Users (everyone)",
        description=("**GET /api/v1/staff/users/** · Bearer (operations, support). Paginated.\n\n"
                     "Search matches phone (with or without the leading 0), name or email."),
        parameters=[OpenApiParameter("role", str, enum=["customer", "driver", "rider", "staff"]),
                    OpenApiParameter("status", str, enum=UserStatus.values),
                    OpenApiParameter("search", str), OpenApiParameter("page", int)],
        responses={200: StaffUserRowSerializer(many=True), **errors(401, 403)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class UserDetailView(APIView):
    permission_classes = [UsersRead]

    @extend_schema(tags=[TAG], summary="User detail",
                   description="**GET /api/v1/staff/users/{id}/** · Bearer (operations, support). Profiles, wallet and last 10 trips.",
                   responses={200: StaffUserDetailSerializer, **errors(401, 403, 404)})
    def get(self, request, pk):
        return Response(StaffUserDetailSerializer(get_object_or_404(User, pk=pk), context={"request": request}).data)


def _set_status(request, pk, new_status: str, reason: str) -> User:
    """Rules, notifications and audit live in apps.staff.services (shared with the web dashboard)."""
    return services.set_user_status(request, get_object_or_404(User, pk=pk), new_status, reason)


class _UserStatusView(APIView):
    """Base for the three status actions; subclasses set `new_status` and `needs_reason`."""
    permission_classes = [UsersWrite]
    new_status = UserStatus.SUSPENDED
    needs_reason = True

    def post(self, request, pk):
        reason = ""
        if self.needs_reason:
            data = ReasonSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            reason = data.validated_data["reason"]
        user = _set_status(request, pk, self.new_status, reason)
        return Response(StaffUserDetailSerializer(user, context={"request": request}).data)


class UserSuspendView(_UserStatusView):
    new_status = UserStatus.SUSPENDED

    @extend_schema(tags=[TAG], summary="Suspend a user",
                   description=("**POST /api/v1/staff/users/{id}/suspend/** · Bearer (operations). Temporary hold in every app "
                                "(the apps show 'account on hold'). Drivers/riders are taken offline."),
                   request=ReasonSerializer, responses={200: StaffUserDetailSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        return super().post(request, pk)


class UserBanView(_UserStatusView):
    new_status = UserStatus.BANNED

    @extend_schema(tags=[TAG], summary="Ban a user",
                   description="**POST /api/v1/staff/users/{id}/ban/** · Bearer (operations). Permanent. Reason is required and audited.",
                   request=ReasonSerializer, responses={200: StaffUserDetailSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        return super().post(request, pk)


class UserReinstateView(_UserStatusView):
    new_status = UserStatus.ACTIVE
    needs_reason = False

    @extend_schema(tags=[TAG], summary="Reinstate a user",
                   description="**POST /api/v1/staff/users/{id}/reinstate/** · Bearer (operations). Back to active. No body.",
                   request=None, responses={200: StaffUserDetailSerializer, **errors(401, 403, 404, 409)})
    def post(self, request, pk):
        return super().post(request, pk)
