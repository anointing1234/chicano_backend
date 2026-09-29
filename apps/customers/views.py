"""
Customer endpoints (User app · Cars and User app · Bikes).

    GET/PATCH  /api/v1/customer/profile/
    GET/POST   /api/v1/customer/places/            PATCH/DELETE /api/v1/customer/places/{id}/
    GET/POST   /api/v1/customer/emergency-contacts/   PATCH/DELETE .../{id}/
"""
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, viewsets

from apps.core.permissions import IsCustomer
from apps.core.schema import errors

from .models import EmergencyContact, SavedPlace
from .serializers import CustomerProfileSerializer, EmergencyContactSerializer, SavedPlaceSerializer


@extend_schema_view(
    get=extend_schema(tags=["Customer"], summary="Get my customer profile",
                      description="**GET /api/v1/customer/profile/** · Bearer (customer). Rating, trips, referral code.",
                      responses={200: CustomerProfileSerializer, **errors(401, 403)}),
    patch=extend_schema(tags=["Customer"], summary="Update customer settings",
                        description="**PATCH /api/v1/customer/profile/** · Bearer (customer). Only `data_saver` is editable here; name/photo via PATCH /auth/me/.",
                        responses={200: CustomerProfileSerializer, **errors(400, 401, 403)}),
)
class CustomerProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsCustomer]
    serializer_class = CustomerProfileSerializer
    http_method_names = ["get", "patch"]

    def get_object(self):
        return self.request.user.customer_profile


def _crud_docs(tag, noun, path, serializer):
    """Shared OpenAPI docs for the two small CRUD resources below."""
    return extend_schema_view(
        list=extend_schema(tags=[tag], summary=f"List {noun}s", description=f"**GET {path}** · Bearer (customer). Returns a plain array (not paginated).",
                           responses={200: serializer(many=True), **errors(401, 403)}),
        create=extend_schema(tags=[tag], summary=f"Add a {noun}", description=f"**POST {path}** · Bearer (customer).",
                             responses={201: serializer, **errors(400, 401, 403)}),
        partial_update=extend_schema(tags=[tag], summary=f"Edit a {noun}", description=f"**PATCH {path}{{id}}/** · Bearer (customer). Send only the fields to change.",
                                     responses={200: serializer, **errors(400, 401, 403, 404)}),
        destroy=extend_schema(tags=[tag], summary=f"Delete a {noun}", description=f"**DELETE {path}{{id}}/** · Bearer (customer).",
                              responses={204: None, **errors(401, 403, 404)}),
        retrieve=extend_schema(tags=[tag], summary=f"Get a {noun}", description=f"**GET {path}{{id}}/** · Bearer (customer).",
                               responses={200: serializer, **errors(401, 403, 404)}),
    )


@_crud_docs("Customer", "saved place", "/api/v1/customer/places/", SavedPlaceSerializer)
class SavedPlaceViewSet(viewsets.ModelViewSet):
    """Home, Work and other saved addresses shown on the Home screen."""
    permission_classes = [IsCustomer]
    serializer_class = SavedPlaceSerializer
    http_method_names = ["get", "post", "patch", "delete"]
    pagination_class = None   # short list: return a plain array

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):   # schema generation has no real user
            return SavedPlace.objects.none()
        return SavedPlace.objects.filter(customer=self.request.user.customer_profile)

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user.customer_profile)


@_crud_docs("Customer", "emergency contact", "/api/v1/customer/emergency-contacts/", EmergencyContactSerializer)
class EmergencyContactViewSet(viewsets.ModelViewSet):
    """Contacts alerted on SOS (max 5)."""
    permission_classes = [IsCustomer]
    serializer_class = EmergencyContactSerializer
    http_method_names = ["get", "post", "patch", "delete"]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EmergencyContact.objects.none()
        return EmergencyContact.objects.filter(customer=self.request.user.customer_profile)

    def perform_create(self, serializer):
        from apps.core.exceptions import ApiError
        if self.get_queryset().count() >= 5:
            raise ApiError("limit_reached", "You can save up to 5 emergency contacts.")
        serializer.save(customer=self.request.user.customer_profile)
