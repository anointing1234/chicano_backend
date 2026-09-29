"""Overview: today's numbers, 7-day chart and the live map. Stats refresh every 30 s, map every 10 s."""
from django.shortcuts import render

from apps.staff import services

from ..access import current_service, is_fragment, staff_area


@staff_area("overview")
def overview(request):
    service = current_service(request)
    data = services.overview(service)
    top = max([d["trips"] for d in data["last_7_days"]] + [1])
    for d in data["last_7_days"]:
        d["height"] = round(d["trips"] / top * 100)       # bar height in % for the CSS chart
    context = {"o": data, "title": "Overview"}
    if is_fragment(request):
        return render(request, "dashboard/partials/overview_stats.html", context)
    context["pins"] = services.live_providers(service)
    return render(request, "dashboard/overview.html", context)


@staff_area("overview")
def live_map(request):
    """Fragment only: the pins on the live map (polled by dashboard.js)."""
    return render(request, "dashboard/partials/live_map.html", {"pins": services.live_providers(current_service(request))})


@staff_area("support")
def sos_banner(request):
    """Fragment only: the red 'open SOS' banner at the top of every page (polled every 10 s)."""
    from apps.support.models import SOSAlert
    return render(request, "dashboard/partials/sos_banner.html",
                  {"open_sos_count": SOSAlert.objects.filter(status="open").count()})
