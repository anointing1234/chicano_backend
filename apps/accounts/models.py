"""
Accounts: ONE user table for everybody (the "combined database").

                         ┌──────────────── accounts.User ────────────────┐
                         │ phone (login), name, email, photo, status     │
                         │ staff_role (null unless Super Admin staff)    │
                         └───┬───────────────────┬───────────────────┬───┘
               customers.CustomerProfile   providers.ProviderProfile   (staff: is_staff + staff_role)
               (User app · Cars / Bikes)   service = car  -> "driver"  (Super Admin web)
                                           service = bike -> "rider"

* Customers and providers log in with their phone number + a one-time SMS code (no passwords).
* Staff log in to the Super Admin with email + password.
* A person can hold several roles: e.g. a rider who also books cars has both profiles.
  `User.roles` returns them, e.g. ["customer", "rider"].
"""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"   # temporary hold (e.g. under investigation)
    BANNED = "banned", "Banned"            # permanent


class StaffRole(models.TextChoices):
    """Super Admin roles. Endpoints check these via `HasStaffRole`."""
    SUPER_ADMIN = "super_admin", "Super admin (everything)"
    OPERATIONS = "operations", "Operations (trips, dispatch, users)"
    SUPPORT = "support", "Support (tickets, lost items, small refunds)"
    COMPLIANCE = "compliance", "Compliance (documents, approvals)"
    FINANCE = "finance", "Finance (payouts, refunds, commission)"


class UserManager(BaseUserManager):
    """Creates users keyed by phone number (E.164, e.g. +2348034125567)."""

    use_in_migrations = True

    def create_user(self, phone: str, password: str | None = None, **extra):
        if not phone:
            raise ValueError("Phone number is required")
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()   # OTP-only accounts have no password
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("staff_role", StaffRole.SUPER_ADMIN)
        return self.create_user(phone, password, **extra)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=20, unique=True, help_text="E.164 format, e.g. +2348034125567")
    email = models.EmailField(null=True, blank=True, unique=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    photo = models.ImageField(upload_to="avatars/", null=True, blank=True)

    status = models.CharField(max_length=12, choices=UserStatus.choices, default=UserStatus.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=255, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    # Staff (Super Admin). Mobile users have is_staff=False and staff_role=None.
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)   # Django flag; business state lives in `status`
    staff_role = models.CharField(max_length=20, choices=StaffRole.choices, null=True, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return f"{self.full_name or self.phone}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def roles(self) -> list[str]:
        """["customer", "driver" | "rider", "staff"] depending on which profiles exist."""
        roles = []
        if hasattr(self, "customer_profile"):
            roles.append("customer")
        if hasattr(self, "provider_profile"):
            roles.append("driver" if self.provider_profile.service == "car" else "rider")
        if self.is_staff and self.staff_role:
            roles.append("staff")
        return roles


class OtpPurpose(models.TextChoices):
    LOGIN = "login", "Login / sign-up"
    CHANGE_PHONE = "change_phone", "Change phone number"


class OTPCode(BaseModel):
    """
    One-time SMS code. Only a hash is stored; the plain code exists only in the SMS
    (and, in DEBUG, in the API response so the Expo dev can test without SMS).
    """
    phone = models.CharField(max_length=20, db_index=True)
    purpose = models.CharField(max_length=20, choices=OtpPurpose.choices, default=OtpPurpose.LOGIN)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()


class AppKind(models.TextChoices):
    """Which of the four mobile apps a device belongs to (drives push routing)."""
    USER_CARS = "user_cars", "User app · Cars"
    USER_BIKES = "user_bikes", "User app · Bikes"
    DRIVER_CARS = "driver_cars", "Driver app · Cars"
    RIDER_BIKES = "rider_bikes", "Rider app · Bikes"


class Device(BaseModel):
    """A phone that should receive push notifications (Expo push token or FCM token)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    app = models.CharField(max_length=20, choices=AppKind.choices)
    platform = models.CharField(max_length=10, choices=[("ios", "iOS"), ("android", "Android")])
    push_token = models.CharField(max_length=255)
    app_version = models.CharField(max_length=20, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["push_token"], name="unique_push_token")]
