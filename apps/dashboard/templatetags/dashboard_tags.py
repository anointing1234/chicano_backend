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


# Status -> colour family (see .badge-* in dashboard.css). Unknown statuses are neutral.
TONE = {
    "active": "good", "approved": "good", "completed": "good", "paid": "good", "succeeded": "good", "live": "good", "resolved": "good", "online": "good", "done": "good",
    "pending": "warn", "under_review": "warn", "documents_pending": "warn", "applied": "warn", "searching": "warn",
    "processing": "warn", "in_review": "warn", "partially_refunded": "warn", "acknowledged": "warn", "open": "warn", "high": "warn",
    "scheduled": "info", "accepted": "info", "arrived": "info", "in_progress": "info", "refunded": "info",
    "suspended": "bad", "banned": "bad", "rejected": "bad", "cancelled": "bad", "failed": "bad", "no_provider": "bad",
    "expired": "bad", "urgent": "bad", "action_needed": "bad", "on_hold": "bad", "inactive": "neutral",
}


@register.simple_tag
def badge(value, text=None):
    return format_html('<span class="badge badge-{}">{}</span>', TONE.get(str(value), "neutral"), text or label(value))


@register.simple_tag
def service_tag(service):
    if not service:
        return ""
    return format_html('<span class="svc svc-{}">{}</span>', service, "Car" if service == "car" else "Bike")


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


# Minimal stroke icons (24×24, currentColor) for the sidebar.
ICONS = {
    "grid": "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    "radar": "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 12l6-6",
    "route": "M6 19a2 2 0 1 0 0-4a2 2 0 0 0 0 4zM18 9a2 2 0 1 0 0-4a2 2 0 0 0 0 4zM8 17h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6",
    "wheel": "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 4v6M5 16l5-3M19 16l-5-3",
    "doc": "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
    "people": "M9 11a3 3 0 1 0 0-6a3 3 0 0 0 0 6zM3 20c0-3 3-5 6-5s6 2 6 5M17 11a2.5 2.5 0 1 0 0-5M18 15c2 .5 3 2 3 5",
    "bank": "M3 10l9-6l9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18",
    "tag": "M3 12V4h8l10 10l-8 8zM7.5 8.5h.01",
    "lifebuoy": "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M6 6l4 4M18 6l-4 4M6 18l4-4M18 18l-4-4",
    "gear": "M12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3a1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8a1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
    "logout": "M15 4h4v16h-4M10 8l-4 4l4 4M6 12h10",
    "chart": "M4 20V10M10 20V4M16 20v-7M22 20H2",
    "gauge": "M12 14l4-4M3.5 17a9 9 0 1 1 17 0M12 14h.01",
    "card": "M3 6h18v12H3zM3 10h18M7 15h4",
    "bag": "M5 8h14l-1 12H6zM9 8V6a3 3 0 0 1 6 0v2",
    "trophy": "M8 4h8v5a4 4 0 0 1-8 0zM8 6H4a3 3 0 0 0 4 4M16 6h4a3 3 0 0 1-4 4M12 13v4M8 20h8",
    "megaphone": "M3 11v2a1 1 0 0 0 1 1h3l6 4V6L7 10H4a1 1 0 0 0-1 1zM17 9a4 4 0 0 1 0 6M8 14l1 5",
    "id": "M3 5h18v14H3zM8 11a2 2 0 1 0 0-.01M5.5 16c.5-1.5 1.5-2 2.5-2s2 .5 2.5 2M14 10h4M14 14h3",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9c-4.5-1-8-4-8-9V6zM9 12l2 2l4-4",
    "list": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    "search": "M11 18a7 7 0 1 0 0-14a7 7 0 0 0 0 14zM21 21l-5-5",
    "bell": "M6 17V11a6 6 0 1 1 12 0v6l2 2H4zM10 21h4",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "chev": "M6 9l6 6l6-6",
    "collapse": "M15 6l-6 6l6 6M20 4v16",
    "plus": "M12 5v14M5 12h14",
    "download": "M12 4v11M7 10l5 5l5-5M4 20h16",
    "check": "M5 12l5 5l9-10",
    "clock": "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 7v5l3 2",
    "user": "M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6",
    "money": "M3 7h18v10H3zM12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6zM6 10v4M18 10v4",
    "filter": "M3 5h18l-7 8v6l-4-2v-4z",
    "arrow": "M5 12h14M13 6l6 6l-6 6",
    "inbox": "M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5",
    "alert": "M12 3l10 18H2zM12 10v5M12 18h.01",
    "more": "M12 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2zM5 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2zM19 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2z",
    "x": "M6 6l12 12M18 6L6 18",
    "chev-left": "M15 6l-6 6l6 6",
    "chev-right": "M9 6l6 6l-6 6",
    "sort": "M8 9l4-4l4 4M8 15l4 4l4-4",
    "sort-up": "M8 14l4-4l4 4",
    "sort-down": "M8 10l4 4l4-4",
    "trend-up": "M4 16l6-6l4 4l6-6M14 8h6v6",
    "trend-down": "M4 8l6 6l4-4l6 6M14 16h6v-6",
    "eye": "M2 12s3.5-7 10-7s10 7 10 7s-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6z",
    "edit": "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
    "ban": "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM5.6 5.6l12.8 12.8",
    "refresh": "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6",
    "info": "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 11v6M12 7.5h.01",
    "calendar": "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
    "external": "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
}


@register.simple_tag
def icon(name, size=18):
    return mark_safe(
        f'<svg width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'<path d="{ICONS.get(name, ICONS["grid"])}"/></svg>')


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
    return format_html('<span class="delta delta-{}" title="Compared with the previous period of the same length">{}{}%'
                       '<span class="sr-only"> {} vs previous period</span></span>', tone,
                       icon("trend-up" if up else "trend-down", 13), abs(value), "up" if up else "down")


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
