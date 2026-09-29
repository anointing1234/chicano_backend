"""
Django forms for the dashboard.

* Money: the database stores kobo (integers). `NairaFieldsMixin` shows those fields in naira
  (₦1,500.00) and converts back to kobo on save, so staff never type kobo.
* Layout: forms list `sections = [(title, help, [field names]), ...]`; record_form.html renders each
  section as a card. Fields not listed go into a final "Other" section.
* Labels, help texts and placeholders are written for the person using the form, not the database.
"""
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.models import StaffRole
from apps.core.roles import ROLE_DESCRIPTIONS
from apps.payments.models import Incentive, PromoCode
from apps.pricing.models import FareRule, RideType, ServiceZone
from apps.support.models import LostItemStatus

User = get_user_model()
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


# =========================================================================== layout helper
class SectionedFormMixin:
    """Gives templates `form.get_sections` → [{title, help, fields: [BoundField]}]."""
    sections: list[tuple[str, str, list[str]]] = []

    def get_sections(self):
        # On/off settings (Active, First ride only…) render as toggle switches;
        # Django's "---------" empty choice reads as "Select…".
        for field in self.fields.values():
            choices = getattr(field, "choices", None)
            if choices is not None and not isinstance(field, forms.ModelChoiceField):
                choices = list(choices)
                if choices and choices[0][0] == "" and set(str(choices[0][1])) <= {"-"}:
                    field.choices = [("", "Select…")] + choices[1:]
            elif isinstance(field, forms.ModelChoiceField) and field.empty_label and set(field.empty_label) <= {"-"}:
                field.empty_label = "Select…"
            if isinstance(field.widget, forms.CheckboxInput) and "switch" not in field.widget.attrs.get("class", ""):
                field.widget.attrs["class"] = (field.widget.attrs.get("class", "") + " switch").strip()
                field.widget.attrs.setdefault("role", "switch")
        used, out = set(), []
        for title, help_text, names in self.sections:
            fields = [self[n] for n in names if n in self.fields]
            used.update(names)
            if fields:
                out.append({"title": title, "help": help_text, "fields": fields})
        rest = [self[n] for n in self.fields if n not in used]
        if rest:
            out.append({"title": "Other", "help": "", "fields": rest})
        return out


class NairaFieldsMixin:
    """List kobo model fields in `money_fields`; they are edited as naira and saved as kobo."""
    money_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.money_fields:
            old = self.fields[name]
            help_text = (old.help_text or "").replace("kobo", "").replace("()", "").strip(" ,")
            self.fields[name] = forms.DecimalField(
                label=old.label, required=old.required, min_value=0, decimal_places=2, max_digits=14, help_text=help_text,
                widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0.00", "data-money": "1"}))
            value = getattr(self.instance, name, None) if getattr(self, "instance", None) and self.instance.pk else self.initial.get(name)
            if value is not None:
                self.initial[name] = (Decimal(value) / 100).quantize(Decimal("0.01"))

    def clean(self):
        data = super().clean()
        for name in self.money_fields:
            if data.get(name) is not None:
                data[name] = int((data[name] * 100).quantize(Decimal("1")))
        return data


# =========================================================================== small action forms
class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True, "placeholder": "you@chicanocruise.com"}),
                             error_messages={"required": "Enter your work email.", "invalid": "Enter a valid email address."})
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
                               error_messages={"required": "Enter your password."})


class ReasonForm(forms.Form):
    """Suspend, ban, reject, cancel, mark failed. The reason is audited (and shown to the person where noted)."""
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 3}))


class NotesForm(forms.Form):
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class RefundForm(forms.Form):
    amount = forms.DecimalField(label="Amount", min_value=Decimal("0.01"), decimal_places=2, max_digits=12,
                                widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Driver took a much longer route"}))
    claw_back = forms.BooleanField(required=False, label="Also deduct from the driver/rider's earnings")

    def kobo(self) -> int:
        return int((self.cleaned_data["amount"] * 100).quantize(Decimal("1")))


class VehicleApproveForm(forms.Form):
    """Pick the ride types a car/bike may serve (choices depend on the vehicle kind)."""
    ride_types = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, label="Ride types")

    def __init__(self, *args, vehicle=None, **kwargs):
        super().__init__(*args, **kwargs)
        types = RideType.objects.filter(service=vehicle.kind, is_active=True).order_by("sort_order") if vehicle else []
        self.fields["ride_types"].choices = [
            (t.code, f"{t.name}{f' (vehicles {t.min_vehicle_year} or newer)' if t.min_vehicle_year else ''}") for t in types]


class TicketReplyForm(forms.Form):
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write a reply…"}))
    internal = forms.BooleanField(required=False, label="Internal note (only staff can see it)")


class TicketUpdateForm(forms.Form):
    status = forms.ChoiceField(choices=[("open", "Open (waiting on us)"), ("pending", "Waiting on the customer"), ("resolved", "Resolved"), ("closed", "Closed")])
    priority = forms.ChoiceField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")])
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label="Unassigned", label="Owner")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = User.objects.filter(is_staff=True, status="active", staff_role__in=["support", "operations", "super_admin"]).order_by("first_name")


class LostItemUpdateForm(forms.Form):
    status = forms.ChoiceField(choices=LostItemStatus.choices, label="Status")
    note = forms.CharField(required=False, max_length=500, label="Note for the ticket",
                           widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Driver has the phone, returning it tomorrow 10am at Lekki Phase 1"}))
    notify_customer = forms.BooleanField(required=False, initial=True, label="Send this update to the customer")


AUDIENCES = [
    ("customers", "All customers"),
    ("customers_car", "Customers who rode a car in the last 90 days"),
    ("customers_bike", "Customers who rode a bike in the last 90 days"),
    ("drivers", "All approved drivers (cars)"),
    ("riders", "All approved riders (bikes)"),
    ("providers_online", "Drivers and riders online right now"),
]


class BroadcastForm(forms.Form):
    """A push + inbox message to a whole audience. The view adds a one-time token against double sends."""
    audience = forms.ChoiceField(choices=AUDIENCES, label="Who gets it", widget=forms.RadioSelect)
    title = forms.CharField(max_length=60, label="Title", widget=forms.TextInput(attrs={"placeholder": "e.g. Heavy rain in Lekki", "data-preview": "#pv-title"}),
                            help_text="Up to 60 characters. Shown in bold on the phone.")
    body = forms.CharField(max_length=180, label="Message", widget=forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Expect longer pickups this evening. Stay safe!", "data-preview": "#pv-body"}),
                           help_text="Up to 180 characters.")
    confirm = forms.BooleanField(label="I've checked the audience and the text", error_messages={"required": "Tick the box to confirm before sending."})
    token = forms.CharField(widget=forms.HiddenInput)


# =========================================================================== catalog ModelForms
class PromoForm(SectionedFormMixin, NairaFieldsMixin, forms.ModelForm):
    money_fields = ("max_discount_amount", "budget_amount")
    # One box for both kinds: a percent (10 = 10%) or, for a fixed promo, naira (300 = ₦300, stored as kobo).
    value = forms.DecimalField(label="Discount value", min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "e.g. 10 or 300"}),
                               help_text="Percent off: a whole number, e.g. 10 for 10%. Fixed amount: naira, e.g. 300 for ₦300.")
    sections = [
        ("The code", "What customers type in the app and what they see.", ["code", "description", "service"]),
        ("Discount", "Promo discounts are paid by Chicano Cruise; drivers and riders still earn the full fare.",
         ["discount_type", "value", "max_discount_amount"]),
        ("Limits", "Leave a limit empty for no limit.", ["usage_limit", "per_user_limit", "budget_amount", "first_ride_only"]),
        ("When it works", "", ["valid_from", "valid_to", "is_active"]),
    ]

    class Meta:
        model = PromoCode
        fields = ["code", "description", "service", "discount_type", "value", "max_discount_amount", "budget_amount",
                  "usage_limit", "per_user_limit", "first_ride_only", "valid_from", "valid_to", "is_active"]
        widgets = {"valid_from": DATETIME, "valid_to": DATETIME,
                   "code": forms.TextInput(attrs={"placeholder": "WELCOME10", "style": "text-transform:uppercase", "autocomplete": "off"}),
                   "description": forms.TextInput(attrs={"placeholder": "e.g. 10% off your first ride (up to ₦1,000)"})}
        labels = {"service": "Works for", "discount_type": "Discount type", "value": "Discount value", "max_discount_amount": "Maximum discount",
                  "budget_amount": "Total budget", "usage_limit": "Total uses allowed", "per_user_limit": "Uses per customer",
                  "first_ride_only": "First ride only", "valid_from": "Starts", "valid_to": "Ends", "is_active": "Active"}
        help_texts = {"service": "Leave empty for both cars and bikes.", "max_discount_amount": "Cap for percent promos.",
                      "budget_amount": "The code stops working once this much has been given away."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].choices = [("", "Cars and bikes")] + [c for c in self.fields["service"].choices if c[0]]
        if self.instance.pk and not self.is_bound:
            v = self.instance.value
            self.initial["value"] = (Decimal(v) / 100).quantize(Decimal("0.01")) if self.instance.discount_type == "flat" else v

    def clean(self):
        data = super().clean()
        value = data.get("value")
        if value is not None and data.get("discount_type") == "percent":
            if value > 100:
                self.add_error("value", "A percent can't be more than 100.")
            elif value != value.to_integral_value():
                self.add_error("value", "Use a whole percent, e.g. 10.")
            else:
                data["value"] = int(value)
        elif value is not None and data.get("discount_type") == "flat":
            data["value"] = int((value * 100).quantize(Decimal("1")))      # naira -> kobo
        if data.get("valid_from") and data.get("valid_to") and data["valid_to"] <= data["valid_from"]:
            self.add_error("valid_to", "The end must be after the start.")
        return data


class IncentiveForm(SectionedFormMixin, NairaFieldsMixin, forms.ModelForm):
    money_fields = ("reward_amount",)
    sections = [
        ("Bonus", "Shown in the Driver app / Rider app under Incentives.", ["name", "service", "description"]),
        ("Target and reward", "The reward is added to their balance when they hit the target in the window.",
         ["target_trips", "reward_amount", "min_acceptance_rate"]),
        ("When", "", ["starts_at", "ends_at", "is_active"]),
    ]

    class Meta:
        model = Incentive
        fields = ["name", "service", "description", "target_trips", "reward_amount", "starts_at", "ends_at", "min_acceptance_rate", "is_active"]
        widgets = {"starts_at": DATETIME, "ends_at": DATETIME,
                   "name": forms.TextInput(attrs={"placeholder": "e.g. Weekend 30"}),
                   "description": forms.TextInput(attrs={"placeholder": "e.g. Complete 30 trips Fri–Sun, earn ₦10,000"})}
        labels = {"service": "For", "target_trips": "Trips to complete", "reward_amount": "Reward", "min_acceptance_rate": "Minimum acceptance rate (%)",
                  "starts_at": "Starts", "ends_at": "Ends", "is_active": "Active"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].choices = [("car", "Drivers (cars)"), ("bike", "Riders (bikes)")]

    def clean(self):
        data = super().clean()
        if data.get("starts_at") and data.get("ends_at") and data["ends_at"] <= data["starts_at"]:
            self.add_error("ends_at", "The end must be after the start.")
        return data


class RideTypeForm(SectionedFormMixin, forms.ModelForm):
    sections = [
        ("Ride option", "What customers pick in the “Choose a ride” sheet.", ["name", "description", "service", "code"]),
        ("Vehicle rules", "", ["seats", "min_vehicle_year", "sort_order", "is_active"]),
    ]

    class Meta:
        model = RideType
        fields = ["code", "service", "name", "description", "seats", "min_vehicle_year", "sort_order", "is_active"]
        widgets = {"code": forms.TextInput(attrs={"placeholder": "e.g. car_premium"}), "name": forms.TextInput(attrs={"placeholder": "e.g. Cruise Premium"}),
                   "description": forms.TextInput(attrs={"placeholder": "e.g. Newer cars, top-rated drivers"})}
        labels = {"code": "Code", "min_vehicle_year": "Oldest vehicle year allowed", "sort_order": "Position in the list", "is_active": "Show in the apps"}
        help_texts = {"code": "Used by the mobile apps. Don't change it after launch.", "min_vehicle_year": "Leave empty for no limit."}


class FareRuleForm(SectionedFormMixin, NairaFieldsMixin, forms.ModelForm):
    money_fields = ("base_amount", "per_km_amount", "per_min_amount", "minimum_amount", "booking_fee_amount",
                    "cancellation_fee_amount", "wait_per_min_amount")
    sections = [
        ("Where it applies", "One active rule per ride type and city.", ["ride_type", "city", "is_active"]),
        ("Fare", "Fare = base + per km × distance + per minute × time + booking fee, at least the minimum, rounded to ₦50.",
         ["base_amount", "per_km_amount", "per_min_amount", "minimum_amount", "booking_fee_amount"]),
        ("Commission", "The platform's share of each completed fare.", ["commission_percent"]),
        ("Cancellations and waiting", "", ["cancellation_fee_amount", "cancellation_grace_seconds", "free_wait_seconds", "wait_per_min_amount"]),
    ]

    class Meta:
        model = FareRule
        fields = ["ride_type", "city", "base_amount", "per_km_amount", "per_min_amount", "minimum_amount", "booking_fee_amount",
                  "commission_percent", "cancellation_fee_amount", "cancellation_grace_seconds", "free_wait_seconds",
                  "wait_per_min_amount", "is_active"]
        labels = {"base_amount": "Base fare", "per_km_amount": "Per kilometre", "per_min_amount": "Per minute", "minimum_amount": "Minimum fare",
                  "booking_fee_amount": "Booking fee", "commission_percent": "Commission (%)", "cancellation_fee_amount": "Cancellation fee",
                  "cancellation_grace_seconds": "Free cancellation window (seconds)", "free_wait_seconds": "Free waiting at pickup (seconds)",
                  "wait_per_min_amount": "Waiting charge per minute after that", "is_active": "Active"}
        help_texts = {"is_active": "Saving an active rule switches off the previous rule for this ride type and city.",
                      "cancellation_grace_seconds": "120 = customers can cancel free for 2 minutes after booking."}
        widgets = {"city": forms.TextInput(attrs={"placeholder": "e.g. Lagos"})}

    def validate_unique(self):
        # "One active rule per ride type + city" is kept by the view: it switches the old rule off in
        # the same transaction instead of rejecting the new one.
        pass


class ServiceZoneForm(SectionedFormMixin, forms.ModelForm):
    sections = [
        ("Zone", "If a service has no active zones in a city, the whole city is allowed.", ["name", "service", "city", "is_active"]),
        ("Boundary", "A rectangle. Copy corner coordinates from Google Maps (right-click → the numbers).",
         ["min_lat", "max_lat", "min_lng", "max_lng"]),
    ]

    class Meta:
        model = ServiceZone
        fields = ["name", "service", "city", "min_lat", "max_lat", "min_lng", "max_lng", "is_active"]
        labels = {"min_lat": "South edge (latitude)", "max_lat": "North edge (latitude)", "min_lng": "West edge (longitude)",
                  "max_lng": "East edge (longitude)", "is_active": "Active"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Lagos Island (bikes)"}), "city": forms.TextInput(attrs={"placeholder": "e.g. Lagos"}),
                   "min_lat": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "6.420000"}),
                   "max_lat": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "6.470000"}),
                   "min_lng": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "3.380000"}),
                   "max_lng": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "3.440000"})}

    def clean(self):
        data = super().clean()
        if data.get("min_lat") is not None and data.get("max_lat") is not None and data["min_lat"] >= data["max_lat"]:
            self.add_error("max_lat", "The north edge must be bigger than the south edge.")
        if data.get("min_lng") is not None and data.get("max_lng") is not None and data["min_lng"] >= data["max_lng"]:
            self.add_error("max_lng", "The east edge must be bigger than the west edge.")
        return data


class StaffMemberForm(SectionedFormMixin, forms.ModelForm):
    """Settings › Staff. Staff live in the same users table (is_staff + staff_role) and sign in with email."""
    password = forms.CharField(required=False, min_length=10, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                               help_text="At least 10 characters. Leave empty to keep the current password.")
    sections = [
        ("Person", "", ["first_name", "last_name", "email", "phone"]),
        ("Access", "What each role can do is listed on the Staff & roles page.", ["staff_role", "password"]),
    ]

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "staff_role"]
        labels = {"email": "Work email (used to sign in)", "staff_role": "Role"}
        help_texts = {"phone": "Every account has a unique phone number, e.g. +2348030000000."}
        widgets = {"phone": forms.TextInput(attrs={"type": "tel", "placeholder": "+2348030000000", "autocomplete": "off"}),
                   "email": forms.EmailInput(attrs={"placeholder": "name@chicanocruise.com", "autocomplete": "off"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["first_name"].required = True
        self.fields["staff_role"].required = True
        self.fields["staff_role"].choices = [(k, f"{label} — {ROLE_DESCRIPTIONS.get(k, '')}") for k, label in StaffRole.choices]
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 10 characters. Share it with them privately."

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        elif not user.pk:
            user.set_unusable_password()
        if commit:
            user.save()
        return user
