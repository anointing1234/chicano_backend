"""
Template helpers for the dashboard.   {% load dashboard_tags %}

    {{ ride.total_amount|naira }}          150000 -> ₦1,500      (the database stores kobo)
    {{ "under_review"|label }}             -> Under review
    {% badge ride.status %}                coloured status pill
    {% service_tag ride.service %}         CAR / BIKE chip
    {{ 92.5|pct }}                         -> 93%
    {{ 48210|num }}                        -> 48,210
    {{ dt|ago }}                           -> 3 min ago
    {% query page=3 %}                     current query string with page replaced (for pager links)
    {% icon "grid" %}                      inline SVG icon
    {{ provider|map_x }} / map_y           position (in %) of a pin on the Lagos schematic map
"""
from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def naira(kobo, decimals=False):
    """Kobo integer -> '₦1,500' ('−₦393' for negatives)."""
    if kobo in (None, ""):
        return "—"
    kobo = int(kobo)
    value = abs(kobo) / 100
    text = f"{value:,.2f}" if decimals else f"{value:,.0f}"
    return ("−₦" if kobo < 0 else "₦") + text


@register.filter
def num(value):
    """1284 -> '1,284'."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@register.filter
def label(value):
    """'under_review' -> 'Under review'."""
    if not value:
        return "—"
    text = str(value).replace("_", " ")
    return text[:1].upper() + text[1:]


@register.filter
def pct(value):
    return "—" if value is None else f"{round(float(value))}%"


@register.filter
def ago(dt):
    if not dt:
        return "—"
    seconds = max(0, int((timezone.now() - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


@register.filter
def km(metres):
    return f"{(metres or 0) / 1000:.1f} km"


@register.filter
def minutes(seconds):
    return f"{round((seconds or 0) / 60)} min"


# Status -> colour family, as in the design boards (DS6 "Status badges"). Unknown statuses are neutral.
#   good = green · warn = orange (searching) · amber = review / waiting / on hold · info = blue
#   bad = red · dark = ink (on trip, banned) · neutral = cream
TONE = {
    "active": "good", "approved": "good", "completed": "good", "paid": "good", "succeeded": "good", "live": "good",
    "resolved": "good", "online": "good", "done": "good", "verified": "good",
    "searching": "warn",
    "pending": "amber", "under_review": "amber", "documents_pending": "amber", "applied": "amber", "in_review": "amber",
    "partially_refunded": "amber", "open": "amber", "high": "amber", "on_hold": "amber", "review": "amber", "normal": "neutral",
    "scheduled": "info", "accepted": "info", "arrived": "info", "processing": "info", "refunded": "info", "acknowledged": "info",
    "in_progress": "dark", "banned": "dark",
    "suspended": "bad", "rejected": "bad", "cancelled": "bad", "failed": "bad", "no_provider": "bad",
    "expired": "bad", "urgent": "bad", "action_needed": "bad",
    "inactive": "neutral", "ended": "neutral", "closed": "neutral", "low": "neutral",
}
# Board wording for a few statuses (the model labels are kept everywhere else).
BADGE_TEXT = {"in_progress": "On trip", "no_provider": "No driver/rider", "on_hold": "On hold"}


@register.simple_tag
def badge(value, text=None):
    key = str(value)
    return format_html('<span class="badge badge-{}">{}</span>', TONE.get(key, "neutral"), text or BADGE_TEXT.get(key) or label(value))


@register.simple_tag
def service_tag(service):
    if not service:
        return ""
    return format_html('<span class="svc svc-{}">{}{}</span>', service, mark_safe(icon_svg("car" if service == "car" else "bike", 15)),
                       "Car" if service == "car" else "Bike")


@register.simple_tag(takes_context=True)
def query(context, **changes):
    """Rebuild the current query string with some keys changed, e.g. {% query page=2 %}."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return "?" + encoded if encoded else "?"


# Stroke icons (24×24, currentColor, stroke 2) — the exact glyphs from the Super Admin design boards (A01–A10, DS6).
# A value is either SVG child markup ("<path .../>") or a bare path "d" string.
ICONS = {
    # sidebar
    "grid": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "people": '<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 13.5a7 7 0 0 1 4 6.5"/>',
    "bike": '<circle cx="5.5" cy="16.5" r="3.5"/><circle cx="18.5" cy="16.5" r="3.5"/><path d="M5.5 16.5 9.5 10h5l4 6.5M14.5 10 13 6h3.5M9.5 10 8 7.5H5"/>',
    "car": '<path d="M5 17H4a1 1 0 0 1-1-1v-3l2-5a2 2 0 0 1 1.9-1.3h10.2A2 2 0 0 1 19 8l2 5v3a1 1 0 0 1-1 1h-1"/><path d="M3 12h18"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/><path d="M9.5 17h5"/>',
    "wrench": '<path d="M14.5 6.5a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.5-1.5a4 4 0 0 1-5-5L13 2z"/>',
    "doc": '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>',
    "route": '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h8a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h8"/>',
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "headset": '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1"/><rect x="17" y="14" width="4" height="6" rx="1"/>',
    "card": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h3"/>',
    "bank": '<path d="M3 10h18L12 4z"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8" r="1.5"/>',
    "gift": '<rect x="3" y="8" width="18" height="4"/><path d="M5 12v9h14v-9M12 8v13M12 8S10 3 7.5 4 8 8 12 8zM12 8s2-5 4.5-4S16 8 12 8z"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "shield": '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "more": '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    # top bar + actions
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "cash": '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 10v4M18 10v4"/>',
    "hourglass": '<path d="M6 3h12M6 21h12M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 9v5M12 17h.01"/>',
    "chev": '<path d="m6 9 6 6 6-6"/>',
    "chev-right": '<path d="m9 6 6 6-6 6"/>',
    "chev-left": '<path d="m15 6-6 6 6 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "megaphone": '<path d="M3 10v4h4l8 5V5L7 10z"/><path d="M19 9a4 4 0 0 1 0 6"/>',
    "ban": '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "message": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    "wallet": '<path d="M4 7h15a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M4 7l12-3v3"/><path d="M16 13.5h.01"/>',
    "id": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M6 16a3 3 0 0 1 6 0M14 10h4M14 14h4"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5h.01"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
    # kept from the previous set (same names as before, design-consistent strokes)
    "wheel": '<circle cx="5.5" cy="16.5" r="3.5"/><circle cx="18.5" cy="16.5" r="3.5"/><path d="M5.5 16.5 9.5 10h5l4 6.5M14.5 10 13 6h3.5M9.5 10 8 7.5H5"/>',
    "lifebuoy": '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1"/><rect x="17" y="14" width="4" height="6" rx="1"/>',
    "gear": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "trophy": '<rect x="3" y="8" width="18" height="4"/><path d="M5 12v9h14v-9M12 8v13M12 8S10 3 7.5 4 8 8 12 8zM12 8s2-5 4.5-4S16 8 12 8z"/>',
    "logout": "M15 4h4v16h-4M10 8l-4 4l4 4M6 12h10",
    "gauge": "M12 14l4-4M3.5 17a9 9 0 1 1 17 0M12 14h.01",
    "bag": "M5 8h14l-1 12H6zM9 8V6a3 3 0 0 1 6 0v2",
    "package": '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>',
    "list": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "collapse": "M15 6l-6 6l6 6M20 4v16",
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "money": '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 10v4M18 10v4"/>',
    "filter": "M3 5h18l-7 8v6l-4-2v-4z",
    "arrow": "M5 12h14M13 6l6 6l-6 6",
    "inbox": "M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5",
    "sort": "M8 9l4-4l4 4M8 15l4 4l4-4",
    "sort-up": "M8 14l4-4l4 4",
    "sort-down": "M8 10l4 4l4-4",
    "trend-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "trend-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "eye": "M2 12s3.5-7 10-7s10 7 10 7s-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6z",
    "edit": "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
    "external": "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
    "pin": '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "box": '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>',
}


def icon_svg(name, size=18, stroke=2) -> str:
    body = ICONS.get(name, ICONS["grid"])
    if not body.startswith("<"):
        body = f'<path d="{body}"/>'
    return (f'<svg width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>')


@register.simple_tag
def icon(name, size=18, stroke=2):
    return mark_safe(icon_svg(name, size, stroke))


# Greater Lagos bounds for the schematic live map (no map tiles needed).
MAP = {"min_lat": 6.38, "max_lat": 6.70, "min_lng": 3.10, "max_lng": 3.70}


@register.filter
def map_x(lng):
    return f"{(float(lng) - MAP['min_lng']) / (MAP['max_lng'] - MAP['min_lng']) * 100:.2f}"


@register.filter
def map_y(lat):
    return f"{(1 - (float(lat) - MAP['min_lat']) / (MAP['max_lat'] - MAP['min_lat'])) * 100:.2f}"


# =========================================================================== added for the redesign
@register.simple_tag(takes_context=True)
def sort_th(context, key, text, align=""):
    """
    Sortable column header: {% sort_th "joined" "Joined" %}. Clicking toggles ?sort=joined / -joined
    (server-side, see access.apply_sort). The view must pass `sort` (current value) in the context.
    """
    current = context.get("sort", "")
    params = context["request"].GET.copy()
    params.pop("page", None)
    if current == key:
        new, state, arrow = f"-{key}", "ascending", "sort-up"
    elif current == f"-{key}":
        new, state, arrow = key, "descending", "sort-down"
    else:
        new, state, arrow = f"-{key}", "none", "sort"
    params["sort"] = new
    cls = ' class="right"' if align == "right" else ""
    return format_html('<th{} aria-sort="{}"><a class="sort" href="?{}">{}<span class="sort-ind">{}</span></a></th>',
                       mark_safe(cls), state, params.urlencode(), text, icon(arrow, 14))


@register.filter
def initials(name):
    parts = [p for p in str(name or "").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


@register.simple_tag
def delta_badge(value, bad_up=False):
    """
    Previous-period comparison chip: ▲ 12% / ▼ 4%. Renders nothing when there's no comparison.
    bad_up=True for metrics where going up is bad (cancellations, failed payments).
    """
    if value is None or value == "":
        return ""
    up = value >= 0
    tone = "bad" if (up == bool(bad_up)) else "good"
    if value == 0:
        tone = "neutral"
    return format_html('<span class="delta delta-{}" title="Compared with the previous period of the same length">{}{}{}%'
                       '<span class="sr-only"> {} vs previous period</span></span>', tone,
                       icon("arrow-up" if up else "arrow-down", 13, 2.6), "+" if up else "−", abs(value), "up" if up else "down")


@register.simple_tag(takes_context=True)
def filters_active(context, *keys):
    """True when any of these query parameters is set (to show a 'Clear filters' link)."""
    get = context["request"].GET
    return any(get.get(k) for k in keys)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag
def elided_pages(page):
    """Page numbers with '…' gaps for the pager: 1 2 … 7 8 9 … 20."""
    return list(page.paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1))


@register.filter
def is_ellipsis(value):
    return not isinstance(value, int)


# =========================================================================== Super Admin boards
@register.filter
def trip_ref(ride_or_id) -> str:
    """Short trip reference shown on the boards, e.g. TR-3FA85F (first 6 hex digits of the ride's UUID)."""
    raw = getattr(ride_or_id, "pk", ride_or_id)
    return "TR-" + str(raw).replace("-", "")[:6].upper()


@register.filter
def masked_phone(phone) -> str:
    """+2348034125567 -> +234 803 ••• 5567"""
    p = str(phone or "")
    if p.startswith("+234") and len(p) >= 14:
        return f"+234 {p[4:7]} ••• {p[-4:]}"
    return p[:-4] + "••••" if len(p) > 6 else p


@register.filter
def spaced_phone(phone) -> str:
    """+2348034125567 -> +234 803 412 5567"""
    p = str(phone or "")
    if p.startswith("+234") and len(p) == 14:
        return f"+234 {p[4:7]} {p[7:10]} {p[10:]}"
    return p


@register.filter
def short_naira(kobo) -> str:
    from ..metrics import short_naira as fmt
    return fmt(kobo)


@register.simple_tag
def ride_badge(ride):
    """Ride status in the boards' words: Searching · Driver/Rider arriving · On trip · Payment failed · Completed …"""
    status = ride.status
    who = "Rider" if ride.service == "bike" else "Driver"
    if getattr(ride, "payment_status", "") == "failed" and status == "completed":
        return format_html('<span class="badge badge-bad">Payment failed</span>')
    words = {"accepted": f"{who} arriving", "arrived": f"{who} at pickup", "in_progress": "On trip",
             "no_provider": f"No {who.lower()} found"}
    return format_html('<span class="badge badge-{}">{}</span>', TONE.get(status, "neutral"), words.get(status) or label(status))


@register.simple_tag
def wait_badge(since, now=None):
    """'Waiting 6:12' chip: amber under 5 minutes, red after."""
    now = now or timezone.now()
    secs = max(0, int((now - since).total_seconds()))
    tone = "bad" if secs >= 300 else "amber"
    return format_html('<span class="badge badge-{} nodot sm">{}</span>', tone, f"{secs // 60}m {secs % 60:02d}s")


@register.simple_tag
def hour_chart(rows, compare=None):
    """
    Grouped bars, cars vs bikes per hour (A01 "Completed trips by hour"): 700×220 viewBox, 5 grid lines,
    12 px bars with 3 px rounded tops, labels every second hour. Each bar has a <title> for hover.
    """
    W, H, LEFT, BASE, TOP = 700, 220, 40, 194, 10
    top = max([max(r["car"], r["bike"]) for r in rows] + [1])
    step = max(1, top // 4)
    nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]
    step = next((n for n in nice if n * 4 >= top), step)
    ymax = step * 4
    parts = [f'<svg class="chart-svg" viewBox="0 0 {W} {H}" role="img" aria-label="Completed trips per hour, cars and bikes">']
    for i in range(5):
        y = BASE - (BASE - TOP) * i / 4
        parts.append(f'<line x1="{LEFT}" x2="{W}" y1="{y:.1f}" y2="{y:.1f}" stroke="#E6E1D8" stroke-width="1"/>'
                     f'<text x="32" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#6B645A">{step * i:,}</text>')
    n = len(rows) or 1
    slot = (W - LEFT) / n
    for i, r in enumerate(rows):
        cx = LEFT + slot * i + slot / 2
        for j, (key, color, name) in enumerate((("car", "#1A78A8", "Cars"), ("bike", "#D9730D", "Bikes"))):
            v = r[key]
            h = (BASE - TOP) * v / ymax
            x = cx - 13 + j * 14
            y = BASE - h
            if v:
                rr = min(3, h)
                parts.append(f'<path class="bar" d="M{x:.1f} {BASE} V{y + rr:.1f} Q{x:.1f} {y:.1f} {x + rr:.1f} {y:.1f} H{x + 12 - rr:.1f} '
                             f'Q{x + 12:.1f} {y:.1f} {x + 12:.1f} {y + rr:.1f} V{BASE} Z" fill="{color}"><title>{r["label"]} · {name}: {v:,}</title></path>')
        if i % 2 == 0:
            parts.append(f'<text x="{cx:.1f}" y="212" text-anchor="middle" font-size="11" fill="#6B645A">{r["label"]}</text>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


@register.filter
def user_ref(user) -> str:
    """Short account reference, e.g. US-20931F (first 6 hex digits of the user's UUID)."""
    return "US-" + str(getattr(user, "pk", user)).replace("-", "")[:6].upper()
