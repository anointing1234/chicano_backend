"""
Super Admin > Settings > Team, the audit log, and "who am I" for the web app.

    GET          /api/v1/staff/me/                 current staff user + role (drives which menu items show)
    GET/POST     /api/v1/staff/team/               staff accounts (super_admin)
    GET/PATCH    /api/v1/staff/team/{id}/          change role / name / reset password (super_admin)
    GET          /api/v1/staff/audit-log/?action=&actor=&target_type=&target_id=&page=   (super_admin)

Staff accounts live in the same users table as everyone else (is_staff=True + staff_role).
They log in with email + password at POST /api/v1/staff/auth/login/.
"""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import StaffUserSerializer
from apps.core.audit import log_action
from apps.core.exceptions import Conflict
from apps.core.models import AuditLog
from apps.core.schema import errors

from ..serializers import AuditLogSerializer, StaffMemberSerializer
from ._common import TAG, AnyStaff, SuperAdminOnly

User = get_user_model()


class StaffMeView(APIView):
    permission_classes = [AnyStaff]

    @extend_schema(tags=[TAG], summary="Current staff user",
                   description=("**GET /api/v1/staff/me/** · Bearer (any staff). Call on web-app load to restore the session "
                                "and decide which menu items to show from `staff_role`."),
                   responses={200: StaffUserSerializer, **errors(401, 403)})
    def get(self, request):
        return Response(StaffUserSerializer(request.user, context={"request": request}).data)


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="Staff accounts", description="**GET /api/v1/staff/team/** · Bearer (super_admin).",
                       responses={200: StaffMemberSerializer(many=True), **errors(401, 403)}),
    create=extend_schema(tags=[TAG], summary="Add a staff account",
                         description=("**POST /api/v1/staff/team/** · Bearer (super_admin). `phone` is required because every "
                                      "user row is keyed by phone; use the person's work number."),
                         responses={201: StaffMemberSerializer, **errors(400, 401, 403)}),
    retrieve=extend_schema(tags=[TAG], summary="Get a staff account", description="**GET /api/v1/staff/team/{id}/** · Bearer (super_admin).",
                           responses={200: StaffMemberSerializer, **errors(401, 403, 404)}),
    partial_update=extend_schema(tags=[TAG], summary="Edit a staff account",
                                 description=("**PATCH /api/v1/staff/team/{id}/** · Bearer (super_admin). Change role/name, or send "
                                              "`password` to reset it. To remove access use POST /staff/users/{id}/suspend/."),
                                 responses={200: StaffMemberSerializer, **errors(400, 401, 403, 404, 409)}),
)
class StaffMemberViewSet(viewsets.ModelViewSet):
    permission_classes = [SuperAdminOnly]
    serializer_class = StaffMemberSerializer
    http_method_names = ["get", "post", "patch"]
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        return User.objects.filter(is_staff=True).order_by("first_name")

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request, "staff.create", user, {"role": user.staff_role})

    def perform_update(self, serializer):
        # Stop the last super admin from demoting themselves and locking everyone out.
        if (serializer.instance.pk == self.request.user.pk and "staff_role" in serializer.validated_data
                and serializer.validated_data["staff_role"] != "super_admin"):
            raise Conflict("cannot_change_self", "Ask another super admin to change your role.")
        user = serializer.save()
        changed = [k if k != "password" else "password (reset)" for k in serializer.validated_data.keys()]
        log_action(self.request, "staff.update", user, {"changed": changed})


class AuditLogListView(generics.ListAPIView):
    permission_classes = [SuperAdminOnly]
    serializer_class = AuditLogSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        p = self.request.query_params
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")
        if p.get("action"):
            qs = qs.filter(action__startswith=p["action"])     # "ride." matches ride.refund, ride.cancel...
        for field in ("actor", "target_type", "target_id"):
            if p.get(field):
                qs = qs.filter(**{field: p[field]})
        return qs

    @extend_schema(tags=[TAG], summary="Audit log",
                   description=("**GET /api/v1/staff/audit-log/** · Bearer (super_admin). Every staff action, newest first. "
                                "`action` is a prefix match (e.g. `ride.` or `provider.approve`)."),
                   parameters=[OpenApiParameter("action", str), OpenApiParameter("actor", str, description="Staff user id"),
                               OpenApiParameter("target_type", str, description="e.g. Ride, ProviderProfile, User"),
                               OpenApiParameter("target_id", str), OpenApiParameter("page", int)],
                   responses={200: AuditLogSerializer(many=True), **errors(401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
