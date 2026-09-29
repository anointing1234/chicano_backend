"""
Super Admin > Promotions, Incentives and Settings (ride types, fares, service zones).

Standard CRUD for each (GET list, POST, GET {id}, PATCH {id}, DELETE {id}):

    /api/v1/staff/promotions/        promo codes (e.g. WELCOME10, BIKE300)       read+write: operations, finance
    /api/v1/staff/incentives/        driver/rider trip bonuses                   read+write: operations, finance
    /api/v1/staff/ride-types/        Standard, XL, Premium, Bike...              read: any staff, write: super_admin
    /api/v1/staff/fare-rules/        prices per ride type + city (kobo)          read: any staff, write: super_admin
    /api/v1/staff/service-zones/     where cars / bikes may operate              read: any staff, write: super_admin

All list endpoints are paginated and accept ?service=car|bike. Every change is written to
the audit log. Records already used by trips can't be deleted (409 `in_use`): set
`is_active=false` instead.
"""
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, ProtectedError
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from apps.core.audit import log_action
from apps.core.exceptions import Conflict
from apps.core.schema import errors
from apps.payments.models import Incentive, PromoCode
from apps.payments.serializers import IncentiveAdminSerializer, PromoAdminSerializer
from apps.pricing.models import FareRule, RideType, ServiceZone
from apps.pricing.serializers import RideTypeSerializer, ServiceZoneSerializer

from ..serializers import StaffFareRuleSerializer as FareRuleSerializer
from ._common import SERVICE_PARAM, TAG, GrowthRoles, ReadVsWritePermissionMixin, SuperAdminOnly, service_filter


def _crud_docs(noun: str, path: str, serializer, who_reads: str, who_writes: str):
    """Same OpenAPI docs shape for every catalog resource, so Swagger reads consistently."""
    return extend_schema_view(
        list=extend_schema(tags=[TAG], summary=f"List {noun}s", parameters=[SERVICE_PARAM],
                           description=f"**GET {path}** · Bearer ({who_reads}). Paginated. Optional `?service=car|bike`.",
                           responses={200: serializer(many=True), **errors(400, 401, 403)}),
        create=extend_schema(tags=[TAG], summary=f"Create a {noun}", description=f"**POST {path}** · Bearer ({who_writes}).",
                             responses={201: serializer, **errors(400, 401, 403)}),
        retrieve=extend_schema(tags=[TAG], summary=f"Get a {noun}", description=f"**GET {path}{{id}}/** · Bearer ({who_reads}).",
                               responses={200: serializer, **errors(401, 403, 404)}),
        partial_update=extend_schema(tags=[TAG], summary=f"Edit a {noun}",
                                     description=f"**PATCH {path}{{id}}/** · Bearer ({who_writes}). Send only changed fields.",
                                     responses={200: serializer, **errors(400, 401, 403, 404)}),
        destroy=extend_schema(tags=[TAG], summary=f"Delete a {noun}",
                              description=f"**DELETE {path}{{id}}/** · Bearer ({who_writes}). 409 `in_use` if trips reference it — deactivate instead.",
                              responses={204: None, **errors(401, 403, 404, 409)}),
    )


class _AuditedCatalogViewSet(ReadVsWritePermissionMixin, viewsets.ModelViewSet):
    """ModelViewSet that audits writes, supports ?service= and blocks deleting records in use."""
    http_method_names = ["get", "post", "patch", "delete"]
    filter_backends = []
    audit_name = "record"          # e.g. "promo" -> audit actions "promo.create", "promo.update", ...
    service_field = "service"      # model field used by ?service= (None = not filterable)

    def filter_by_service(self, qs):
        service = service_filter(self.request)
        if service and self.service_field:
            qs = qs.filter(**{self.service_field: service})
        return qs

    def perform_create(self, serializer):
        obj = serializer.save()
        # Round-trip through DjangoJSONEncoder so UUIDs/Decimals/dates are JSON-safe for AuditLog.data.
        log_action(self.request, f"{self.audit_name}.create", obj, {"data": json.loads(json.dumps(serializer.data, cls=DjangoJSONEncoder))})

    def perform_update(self, serializer):
        obj = serializer.save()
        log_action(self.request, f"{self.audit_name}.update", obj, {"changed": list(serializer.validated_data.keys())})

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise Conflict("in_use", "This is used by existing trips. Set is_active=false instead.")
        log_action(self.request, f"{self.audit_name}.delete", instance)


@_crud_docs("promo code", "/api/v1/staff/promotions/", PromoAdminSerializer, "operations, finance", "operations, finance")
class PromotionViewSet(_AuditedCatalogViewSet):
    """Promo codes. `value` is a percent (discount_type=percent) or kobo (discount_type=flat). Promos are platform-funded."""
    read_permission = write_permission = GrowthRoles
    serializer_class = PromoAdminSerializer
    audit_name = "promo"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PromoCode.objects.none()
        qs = PromoCode.objects.annotate(redemptions_count=Count("redemptions")).order_by("-created_at")
        return self.filter_by_service(qs)


@_crud_docs("incentive", "/api/v1/staff/incentives/", IncentiveAdminSerializer, "operations, finance", "operations, finance")
class IncentiveViewSet(_AuditedCatalogViewSet):
    """Trip-count bonuses, e.g. 'Complete 40 bike trips Mon–Sun, earn ₦5,000'."""
    read_permission = write_permission = GrowthRoles
    serializer_class = IncentiveAdminSerializer
    audit_name = "incentive"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Incentive.objects.none()
        return self.filter_by_service(Incentive.objects.order_by("-starts_at"))


@_crud_docs("ride type", "/api/v1/staff/ride-types/", RideTypeSerializer, "any staff", "super_admin")
class RideTypeViewSet(_AuditedCatalogViewSet):
    """The options in the 'Choose a ride' sheet. `code` is stable and used by the apps; change `name` freely."""
    write_permission = SuperAdminOnly
    serializer_class = RideTypeSerializer
    audit_name = "ride_type"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RideType.objects.none()
        return self.filter_by_service(RideType.objects.order_by("service", "sort_order"))


@_crud_docs("fare rule", "/api/v1/staff/fare-rules/", FareRuleSerializer, "any staff", "super_admin")
class FareRuleViewSet(_AuditedCatalogViewSet):
    """
    Prices, all in kobo. fare = base + per_km × km + per_min × min (+ booking fee), at least `minimum_amount`,
    rounded to ₦50. Only one active rule per ride type + city; new quotes use it immediately.
    """
    write_permission = SuperAdminOnly
    serializer_class = FareRuleSerializer
    audit_name = "fare_rule"
    service_field = "ride_type__service"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FareRule.objects.none()
        return self.filter_by_service(FareRule.objects.select_related("ride_type").order_by("ride_type__service", "ride_type__sort_order"))

    @transaction.atomic
    def perform_create(self, serializer):
        self._deactivate_others(serializer)
        super().perform_create(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        self._deactivate_others(serializer)
        super().perform_update(serializer)

    def _deactivate_others(self, serializer):
        """Keep the 'one active rule per ride type + city' invariant when saving an active rule."""
        data = serializer.validated_data
        instance = serializer.instance
        is_active = data.get("is_active", instance.is_active if instance else True)
        if not is_active:
            return
        ride_type = data.get("ride_type", instance.ride_type if instance else None)
        city = data.get("city", instance.city if instance else None)
        others = FareRule.objects.filter(ride_type=ride_type, city=city, is_active=True)
        if instance:
            others = others.exclude(pk=instance.pk)
        others.update(is_active=False)


@_crud_docs("service zone", "/api/v1/staff/service-zones/", ServiceZoneSerializer, "any staff", "super_admin")
class ServiceZoneViewSet(_AuditedCatalogViewSet):
    """
    Rectangles (lat/lng bounds) where a service operates. If a city has no zones for a service,
    the whole city is allowed. Bookings outside every active zone get 400 `outside_service_zone`.
    """
    write_permission = SuperAdminOnly
    serializer_class = ServiceZoneSerializer
    audit_name = "service_zone"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ServiceZone.objects.none()
        return self.filter_by_service(ServiceZone.objects.order_by("service", "name"))
