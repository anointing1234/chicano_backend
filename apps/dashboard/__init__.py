"""
Super Admin web dashboard: server-rendered Django templates + plain HTML/CSS/JavaScript.

    /dashboard/            login with a staff email + password (Django session)
    /dashboard/...         Overview, Dispatch, Trips, Drivers & riders, Documents, Users,
                           Payouts, Promotions, Support & SOS, Settings

How it's built (all standard Django, no front-end framework):
    views/        one module per page; function-based views, POST → redirect → GET
    forms.py      Django forms (ModelForms for promos, fares, ... with naira ↔ kobo conversion)
    access.py     @staff_area("...") decorator, the All/Cars/Bikes switch, pagination, action helper
    templates/    base.html + one template per page; partials/ are also served alone for live refresh
    static/       dashboard.css and dashboard.js (live refresh, dialogs, confirmations)

Business rules are NOT written here: every action calls apps/staff/services.py (shared with the
/api/v1/staff/ JSON API) and roles come from apps/core/roles.py.
"""
