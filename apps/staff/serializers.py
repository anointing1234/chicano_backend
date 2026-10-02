"""
Serializers for the Super Admin web app (/api/v1/staff/...).

Staff see more than the mobile apps do (phone numbers, bank details, internal notes,
commission), so these are kept separate from the app-facing serializers. Nothing here is
ever returned to a customer, driver or rider.

Money is always integer kobo (₦1 = 100 kobo), same as the rest of the API.
"""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import StaffRole
from apps.core.models import AuditLog
from apps.payments.models import Payout
from apps.pricing.serializers import FareRuleSerializer
from apps.providers.models import ProviderDocument, ProviderProfile
from apps.providers.serializers import VehicleSerializer
from apps.rides.models import Ride, RideOffer
from apps.rides.serializers import RideEventSerializer, RideStopSerializer
from apps.support.models import SOSAlert, SupportTicket, TicketMessage

User = get_user_model()


# =========================================================================== small shared pieces
class PersonSerializer(serializers.ModelSerializer):
    """Compact person block reused in rows (customer on a ride, provider's user, ticket owner...)."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "phone", "email", "photo", "status"]


class ReasonSerializer(serializers.Serializer):
    """Body for suspend / ban / reject / cancel actions. The reason is stored and shown in the audit log."""
    reason = serializers.CharField(max_length=255)


# =========================================================================== overview (dashboard)
class OverviewDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    trips = serializers.IntegerField()
    gross_amount = serializers.IntegerField()


class OverviewSerializer(serializers.Serializer):
    """Top-of-dashboard numbers. `service` echoes the filter: all | car | bike."""
    service = serializers.CharField()
    trips_today = serializers.IntegerField(help_text="Completed trips since midnight (Africa/Lagos).")
    cancelled_today = serializers.IntegerField()
    active_trips = serializers.IntegerField(help_text="searching + accepted + arrived + in_progress right now.")
    gross_today_amount = serializers.IntegerField(help_text="Sum of completed fares today (kobo).")
    commission_today_amount = serializers.IntegerField(help_text="Platform revenue today (kobo).")
    online_providers = serializers.IntegerField()
    providers_awaiting_review = serializers.IntegerField()
    documents_pending = serializers.IntegerField()
    needs_manual_dispatch = serializers.IntegerField()
    open_sos = serializers.IntegerField()
    open_tickets = serializers.IntegerField()
    customers_total = serializers.IntegerField()
    new_customers_today = serializers.IntegerField()
    last_7_days = OverviewDaySerializer(many=True)


# =========================================================================== ride rows (used by users + trips)
class StaffRideRowSerializer(serializers.ModelSerializer):
    """One row of Super Admin > Trips."""
    ride_type_name = serializers.CharField(source="ride_type.name", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    provider_name = serializers.CharField(source="provider.user.full_name", read_only=True, default=None)

    class Meta:
        model = Ride
        fields = ["id", "service", "status", "ride_type_name", "customer_name", "provider_name", "pickup_address",
                  "dropoff_address", "total_amount", "tip_amount", "commission_amount", "refunded_amount",
                  "payment_method", "payment_status", "needs_manual_dispatch", "requested_at", "completed_at"]


# =========================================================================== users (combined table)
class StaffUserRowSerializer(serializers.ModelSerializer):
    """One row of Super Admin > Users. Every person in the combined users table appears here."""
    full_name = serializers.CharField(read_only=True)
    roles = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "phone", "email", "status", "status_reason", "roles", "staff_role", "date_joined"]


class StaffUserDetailSerializer(StaffUserRowSerializer):
    """User detail drawer: profiles, wallet and recent trips in one call."""
    customer = serializers.SerializerMethodField()
    provider_id = serializers.SerializerMethodField(help_text="Open /staff/providers/{provider_id}/ for the driver/rider side.")
    wallet_balance_amount = serializers.SerializerMethodField()
    recent_rides = serializers.SerializerMethodField()

    class Meta(StaffUserRowSerializer.Meta):
        fields = StaffUserRowSerializer.Meta.fields + ["photo", "phone_verified_at", "customer", "provider_id",
                                                        "wallet_balance_amount", "recent_rides"]

    def get_customer(self, obj) -> dict | None:
        cp = getattr(obj, "customer_profile", None)
        if not cp:
            return None
        return {"rating_avg": cp.rating_avg, "rating_count": cp.rating_count, "total_trips": cp.total_trips,
                "referral_code": cp.referral_code}

    def get_provider_id(self, obj) -> str | None:
        pp = getattr(obj, "provider_profile", None)
        return str(pp.id) if pp else None

    def get_wallet_balance_amount(self, obj) -> int | None:
        wallet = getattr(obj, "wallet", None)
        return wallet.balance_amount if wallet else None

    @extend_schema_field(StaffRideRowSerializer(many=True))
    def get_recent_rides(self, obj):
        return StaffRideRowSerializer(obj.rides.select_related("ride_type", "provider__user")[:10], many=True).data


# =========================================================================== drivers & riders
class StaffProviderRowSerializer(serializers.ModelSerializer):
    """One row of Super Admin > Drivers & riders. `kind` = driver (car) or rider (bike)."""
    user = PersonSerializer(read_only=True)
    kind = serializers.CharField(read_only=True)
    acceptance_rate = serializers.FloatField(read_only=True)
    cancellation_rate = serializers.FloatField(read_only=True)
    documents_pending = serializers.IntegerField(read_only=True, default=0, help_text="Annotated count of pending documents.")

    class Meta:
        model = ProviderProfile
        fields = ["id", "user", "service", "kind", "status", "status_reason", "city", "is_online", "rating_avg",
                  "total_trips", "acceptance_rate", "cancellation_rate", "documents_pending", "created_at", "approved_at"]


class StaffDocumentSerializer(serializers.ModelSerializer):
    """A driver/rider document in the review queue. `file` is a full URL the web app can open."""
    provider_id = serializers.UUIDField(source="provider.id", read_only=True)
    provider_name = serializers.CharField(source="provider.user.full_name", read_only=True)
    service = serializers.CharField(source="provider.service", read_only=True)
    doc_type_label = serializers.CharField(source="get_doc_type_display", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True, default=None)

    class Meta:
        model = ProviderDocument
        fields = ["id", "provider_id", "provider_name", "service", "doc_type", "doc_type_label", "file", "back_file", "number",
                  "expires_at", "status", "rejection_reason", "reviewed_by_name", "reviewed_at", "created_at"]


class StaffProviderDetailSerializer(StaffProviderRowSerializer):
    """Provider detail: vehicles, documents, bank details, balance and onboarding checklist."""
    vehicles = VehicleSerializer(many=True, read_only=True)
    documents = StaffDocumentSerializer(many=True, read_only=True)
    balance_amount = serializers.SerializerMethodField(help_text="What we owe them now (negative = they owe commission).")
    checklist = serializers.SerializerMethodField()

    class Meta(StaffProviderRowSerializer.Meta):
        fields = StaffProviderRowSerializer.Meta.fields + [
            "date_of_birth", "rating_count", "offers_received", "offers_accepted", "trips_cancelled",
            "last_lat", "last_lng", "last_location_at", "bank_name", "bank_account_number", "bank_account_name",
            "vehicles", "documents", "balance_amount", "checklist"]

    def get_balance_amount(self, obj) -> int:
        from apps.payments.services import provider_balance
        return provider_balance(obj)

    def get_checklist(self, obj) -> dict:
        from apps.providers.services import onboarding_checklist
        return onboarding_checklist(obj)


class LiveProviderSerializer(serializers.ModelSerializer):
    """Pin on the live map (Dispatch / Overview)."""
    name = serializers.CharField(source="user.full_name")
    kind = serializers.CharField(read_only=True)
    on_trip = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = ProviderProfile
        fields = ["id", "name", "service", "kind", "last_lat", "last_lng", "last_heading", "last_location_at", "on_trip", "rating_avg"]


class VehicleApproveSerializer(serializers.Serializer):
    ride_types = serializers.ListField(
        child=serializers.CharField(), allow_empty=False,
        help_text='Ride type codes this vehicle may serve, e.g. ["car_standard", "car_xl"] or ["bike"].')


# =========================================================================== rides
class StaffOfferSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source="provider.user.full_name", read_only=True)

    class Meta:
        model = RideOffer
        fields = ["id", "provider", "provider_name", "status", "distance_to_pickup_m", "eta_to_pickup_s", "is_manual",
                  "expires_at", "responded_at", "created_at"]


class StaffRideDetailSerializer(serializers.ModelSerializer):
    """Everything about one trip: people, money, timeline (events) and dispatch attempts (offers)."""
    customer = PersonSerializer(read_only=True)
    provider = StaffProviderRowSerializer(read_only=True)
    vehicle = VehicleSerializer(read_only=True)
    ride_type_name = serializers.CharField(source="ride_type.name", read_only=True)
    stops = RideStopSerializer(many=True, read_only=True)
    events = RideEventSerializer(many=True, read_only=True)
    offers = StaffOfferSerializer(many=True, read_only=True)
    ratings = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = ["id", "service", "status", "city", "ride_type_name", "customer", "provider", "vehicle",
                  "pickup_lat", "pickup_lng", "pickup_address", "pickup_note", "dropoff_lat", "dropoff_lng", "dropoff_address",
                  "stops", "scheduled_for", "distance_m", "duration_s",
                  "gross_amount", "discount_amount", "wait_charge_amount", "total_amount", "tip_amount", "commission_amount",
                  "cancellation_fee_amount", "refunded_amount", "payment_method", "payment_status", "cash_collected_at",
                  "package_kind", "package_size", "package_contents", "package_fragile", "recipient_name", "recipient_phone",
                  "package_collected_at", "helmet_handed_over_at", "helmet_returned_at", "needs_manual_dispatch",
                  "requested_at", "accepted_at", "arrived_at", "started_at", "completed_at", "cancelled_at", "cancelled_by",
                  "cancel_reason", "events", "offers", "ratings"]

    def get_ratings(self, obj) -> list[dict]:
        return [{"direction": r.direction, "stars": r.stars, "tags": r.tags, "comment": r.comment} for r in obj.ratings.all()]


class RefundSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1, help_text="kobo. Max = total + tip - already refunded.")
    reason = serializers.CharField(max_length=255)
    claw_back = serializers.BooleanField(default=False, help_text="Also deduct the amount from the driver/rider's earnings.")


class AssignSerializer(serializers.Serializer):
    provider_id = serializers.UUIDField(help_text="From GET /staff/dispatch/{ride_id}/candidates/.")


class CandidateSerializer(serializers.Serializer):
    """A driver/rider who could take a waiting ride, nearest first."""
    provider_id = serializers.UUIDField()
    name = serializers.CharField()
    phone = serializers.CharField()
    distance_km = serializers.FloatField()
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2)
    acceptance_rate = serializers.FloatField()
    vehicle = serializers.CharField(allow_null=True)


# =========================================================================== payouts
class StaffPayoutSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source="provider.user.full_name", read_only=True)
    provider_phone = serializers.CharField(source="provider.user.phone", read_only=True)
    service = serializers.CharField(source="provider.service", read_only=True)
    processed_by_name = serializers.CharField(source="processed_by.full_name", read_only=True, default=None)

    class Meta:
        model = Payout
        fields = ["id", "provider", "provider_name", "provider_phone", "service", "amount", "fee_amount", "method", "status",
                  "bank_name", "bank_account_number", "bank_account_name", "reference", "failure_reason", "paid_at",
                  "processed_by_name", "created_at"]


class MarkPaidSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=80, required=False, allow_blank=True, default="",
                                      help_text="Bank transfer reference.")


class RunPayoutsResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    total_amount = serializers.IntegerField()
    payouts = StaffPayoutSerializer(many=True)


# =========================================================================== settings
class StaffFareRuleSerializer(FareRuleSerializer):
    """
    Same fields as the public FareRuleSerializer, minus DRF's auto unique validator for the
    "one active rule per ride type + city" constraint: the staff view deactivates the old
    rule in the same transaction instead of rejecting the new one.
    """

    class Meta(FareRuleSerializer.Meta):
        validators = []


# =========================================================================== support + safety
class StaffTicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    from_staff = serializers.BooleanField(source="sender.is_staff", read_only=True)

    class Meta:
        model = TicketMessage
        fields = ["id", "sender_name", "from_staff", "body", "attachment", "is_internal_note", "created_at"]


class StaffTicketSerializer(serializers.ModelSerializer):
    """Staff view of a ticket: includes internal notes and who it's assigned to."""
    user = PersonSerializer(read_only=True)
    messages = StaffTicketMessageSerializer(many=True, read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True, default=None)

    class Meta:
        model = SupportTicket
        fields = ["id", "user", "ride", "category", "subject", "status", "priority", "assigned_to", "assigned_to_name",
                  "messages", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "ride", "category", "subject", "messages", "created_at", "updated_at"]


class StaffTicketUpdateSerializer(serializers.ModelSerializer):
    """PATCH a ticket: change status, priority or assignee."""

    class Meta:
        model = SupportTicket
        fields = ["status", "priority", "assigned_to"]

    def validate_assigned_to(self, value):
        if value and not value.is_staff:
            raise serializers.ValidationError("Tickets can only be assigned to staff.")
        return value


class StaffTicketReplySerializer(serializers.Serializer):
    body = serializers.CharField()
    internal = serializers.BooleanField(default=False, help_text="True = internal note, never shown in the apps.")
    status = serializers.ChoiceField(choices=["open", "pending", "resolved", "closed"], required=False,
                                     help_text="Optionally change the status in the same call (usually 'pending' or 'resolved').")


class StaffSOSSerializer(serializers.ModelSerializer):
    raised_by = PersonSerializer(read_only=True)
    ride_status = serializers.CharField(source="ride.status", read_only=True, default=None)
    service = serializers.CharField(source="ride.service", read_only=True, default=None)
    handled_by_name = serializers.CharField(source="handled_by.full_name", read_only=True, default=None)

    class Meta:
        model = SOSAlert
        fields = ["id", "raised_by", "ride", "ride_status", "service", "lat", "lng", "status", "contacts_notified",
                  "handled_by_name", "notes", "resolved_at", "created_at"]


class SOSActionSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="", help_text="Appended to the alert's notes.")


# =========================================================================== staff accounts + audit
class StaffMemberSerializer(serializers.ModelSerializer):
    """Super Admin > Settings > Team. Create staff with email + password; they log in at /staff/auth/login/."""
    full_name = serializers.CharField(read_only=True)
    password = serializers.CharField(write_only=True, min_length=10, required=False,
                                     help_text="Required on create. Min 10 characters.")

    class Meta:
        model = User
        fields = ["id", "full_name", "first_name", "last_name", "email", "phone", "staff_role", "status", "password",
                  "last_login", "date_joined"]
        read_only_fields = ["id", "full_name", "status", "last_login", "date_joined"]
        extra_kwargs = {"email": {"required": True, "allow_null": False}, "staff_role": {"required": True, "allow_null": False}}

    def validate_staff_role(self, value):
        if value not in StaffRole.values:
            raise serializers.ValidationError("Unknown role.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": ["Required when creating a staff account."]})
        return attrs

    def create(self, validated):
        password = validated.pop("password")
        return User.objects.create_user(password=password, is_staff=True, **validated)

    def update(self, instance, validated):
        password = validated.pop("password", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default=None)
    actor_role = serializers.CharField(source="actor.staff_role", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_name", "actor_role", "action", "target_type", "target_id", "data", "ip_address", "created_at"]
