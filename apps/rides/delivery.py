"""
Dispatch (package delivery) details for bike rides.

Bikes on Chicano Cruise carry packages, not passengers. Every bike booking needs:
    what is being sent (kind, size, optional contents, fragile) and who receives it (name + phone).

The customer app sends these as `package` on POST /rides/. Older builds wrote them into `pickup_note` instead:

    "PACKAGE: Documents, Small, fragile (Signed contract) | TO: Ngozi 08034125567 | NOTE: Blue gate"

`package_from_request` accepts either, so old and new app builds both work. `pickup_note` is always stored as sent,
so a rider app that still reads the note keeps working too.
"""
import re

from django.db import models

from apps.core.exceptions import ApiError


class PackageKind(models.TextChoices):
    DOCUMENTS = "Documents", "Documents"
    PARCEL = "Parcel", "Parcel"
    FOOD = "Food", "Food"
    GROCERIES = "Groceries", "Groceries"
    ELECTRONICS = "Electronics", "Electronics"
    OTHER = "Other", "Other"


class PackageSize(models.TextChoices):
    SMALL = "Small", "Small · fits in a bag"
    MEDIUM = "Medium", "Medium · shoebox"
    LARGE = "Large", "Large · delivery box, up to about 10 kg"


_NOTE_PREFIX = "PACKAGE: "


def parse_delivery_note(note: str) -> dict | None:
    """Read a note written by the customer app's `buildDeliveryNote`. None when the note isn't one."""
    text = (note or "").strip()
    if not text.startswith(_NOTE_PREFIX):
        return None
    parts = text.split(" | ")

    def pick(label):
        for p in parts:
            if p.startswith(f"{label}: "):
                return p[len(label) + 2:].strip()
        return ""

    what, to = pick("PACKAGE"), pick("TO")
    m = re.match(r"^([^(]*?)(?:\s*\((.*)\))?$", what)
    bits = [b.strip() for b in (m.group(1) if m else what).split(",") if b.strip()]
    phone_m = re.search(r"(\+?\d{10,14})$", to)
    phone = phone_m.group(1) if phone_m else ""
    return {
        "kind": bits[0] if bits and bits[0] in PackageKind.values else "",
        "size": bits[1] if len(bits) > 1 and bits[1] in PackageSize.values else "",
        "contents": (m.group(2) or "") if m else "",
        "fragile": "fragile" in [b.lower() for b in bits[2:]],
        "recipient_name": (to[: -len(phone)] if phone else to).strip(),
        "recipient_phone": phone,
    }


def package_from_request(package: dict | None, pickup_note: str) -> dict:
    """
    The package fields to store on a bike ride, from `package` (new apps) or the note (older apps).
    Raises 400 `package_required` when neither has the recipient and what's being sent.
    """
    from apps.accounts.services import normalize_phone

    data = dict(package or {}) or (parse_delivery_note(pickup_note) or {})
    missing = {}
    if not data.get("kind"):
        missing["kind"] = ["Say what you're sending."]
    if not data.get("size"):
        missing["size"] = ["Choose a size."]
    if len((data.get("recipient_name") or "").strip()) < 2:
        missing["recipient_name"] = ["Who should receive the package?"]
    if not data.get("recipient_phone"):
        missing["recipient_phone"] = ["Add the recipient's phone number."]
    if missing:
        raise ApiError("package_required", "Add the package and recipient details.", details={"package": missing})
    try:
        phone = normalize_phone(data["recipient_phone"])
    except ApiError:
        raise ApiError("invalid_phone", "Enter the recipient's Nigerian phone number, e.g. 0803 412 5567.",
                       details={"package": {"recipient_phone": ["Enter a valid Nigerian phone number."]}})
    return {
        "package_kind": data["kind"],
        "package_size": data["size"],
        "package_contents": (data.get("contents") or "").strip()[:120],
        "package_fragile": bool(data.get("fragile")),
        "recipient_name": data["recipient_name"].strip()[:80],
        "recipient_phone": phone,
    }


def package_payload(ride) -> dict | None:
    """The `package` object returned to the apps (null for car rides and old rides without details)."""
    if ride.service != "bike" or not ride.package_kind:
        return None
    return {
        "kind": ride.package_kind,
        "size": ride.package_size,
        "contents": ride.package_contents,
        "fragile": ride.package_fragile,
        "recipient_name": ride.recipient_name,
        "recipient_phone": ride.recipient_phone,
        "collected_at": ride.package_collected_at,
    }


def landmark_note(ride) -> str:
    """The sender's own landmark note, without the package part an older app may have written into it."""
    parsed_from = (ride.pickup_note or "").strip()
    if not parsed_from.startswith(_NOTE_PREFIX):
        return parsed_from
    i = parsed_from.find(" | NOTE: ")
    return parsed_from[i + len(" | NOTE: "):].strip() if i >= 0 else ""
