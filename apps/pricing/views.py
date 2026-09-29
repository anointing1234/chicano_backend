"""GET /api/v1/ride-types/?service=car|bike: what can be booked (used by both user apps)."""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.core.schema import errors

from .models import RideType
from .serializers import RideTypeSerializer


class RideTypeListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = RideTypeSerializer
    pagination_class = None

    @extend_schema(
        tags=["Booking"],
        summary="List ride types",
        description=("**GET /api/v1/ride-types/?service=car|bike** · Bearer token.\n\n"
                     "User app · Cars sends `service=car` (Standard, XL, Premium). User app · Bikes sends `service=bike`."),
        parameters=[OpenApiParameter("service", str, enum=["car", "bike"], required=False)],
        responses={200: RideTypeSerializer(many=True), **errors(401)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = RideType.objects.filter(is_active=True)
        service = self.request.query_params.get("service")
        return qs.filter(service=service) if service in ("car", "bike") else qs
