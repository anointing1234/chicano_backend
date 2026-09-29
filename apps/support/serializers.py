from rest_framework import serializers

from .models import LostItemReport, Notification, SOSAlert, SupportTicket, TicketMessage


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "title", "body", "data", "is_read", "read_at", "created_at"]

    def get_is_read(self, obj) -> bool:
        return obj.read_at is not None


class TicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    from_staff = serializers.SerializerMethodField()

    class Meta:
        model = TicketMessage
        fields = ["id", "sender_name", "from_staff", "body", "attachment", "created_at"]
        read_only_fields = ["id", "sender_name", "from_staff", "created_at"]

    def get_from_staff(self, obj) -> bool:
        return obj.sender.is_staff


class TicketSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ["id", "ride", "category", "subject", "status", "priority", "messages", "created_at", "updated_at"]
        read_only_fields = ["id", "status", "priority", "messages", "created_at", "updated_at"]

    def get_messages(self, obj) -> TicketMessageSerializer(many=True):
        # Customers/providers never see internal staff notes.
        return TicketMessageSerializer(obj.messages.filter(is_internal_note=False), many=True).data


class TicketCreateSerializer(serializers.ModelSerializer):
    message = serializers.CharField(write_only=True, help_text="First message describing the problem.")

    class Meta:
        model = SupportTicket
        fields = ["ride", "category", "subject", "message"]


class TicketReplySerializer(serializers.Serializer):
    body = serializers.CharField()


class LostItemSerializer(serializers.ModelSerializer):
    ticket_id = serializers.UUIDField(source="ticket.id", read_only=True, default=None)

    class Meta:
        model = LostItemReport
        fields = ["id", "ride", "category", "description", "contact_phone", "status", "ticket_id", "created_at"]
        read_only_fields = ["id", "status", "ticket_id", "created_at"]


class SOSRequestSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)


class SOSAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = SOSAlert
        fields = ["id", "ride", "status", "lat", "lng", "contacts_notified", "created_at"]


class PublicTripSerializer(serializers.Serializer):
    """What someone opening a shared trip link sees (no phone numbers, no fares)."""
    status = serializers.CharField()
    service = serializers.CharField()
    pickup_address = serializers.CharField()
    dropoff_address = serializers.CharField()
    provider_first_name = serializers.CharField(allow_null=True)
    vehicle = serializers.CharField(allow_null=True, help_text="e.g. 'Silver Toyota Corolla · KJA 482 FT'")
    location = serializers.DictField(allow_null=True)
    updated_at = serializers.DateTimeField()
