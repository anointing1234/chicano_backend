"""
Super Admin API views, split by dashboard area:

    overview.py   dashboard numbers, live map
    users.py      the combined users table (customers, drivers, riders, staff)
    providers.py  drivers & riders, vehicles, document review
    rides.py      trips, refunds, staff cancellation, manual dispatch
    finance.py    payouts
    catalog.py    promotions, incentives, ride types, fare rules, service zones
    support.py    tickets, SOS alerts
    team.py       staff accounts, audit log, /staff/me/

_common.py holds the role matrix and the ?service= filter used everywhere.
"""
