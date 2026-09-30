# Chicano Cruise platform

The Django backend for Chicano Cruise's four mobile apps, plus the Super Admin web dashboard
(plain Django templates + HTML/CSS/JavaScript, no front-end framework):

| Mobile app (React Native / Expo, separate repo) | Talks to |
|---|---|
| User app · Cars, User app · Bikes | `/api/v1/auth`, `/customer`, `/ride-types`, `/rides`, `/wallet`, `/support` |
| Driver app · Cars, Rider app · Bikes | `/api/v1/auth`, `/provider/...`, `/support` |
| **Super Admin dashboard** (this repo, `apps/dashboard`) | HTML pages at `/dashboard/` (Django views + templates) |

```
React Native Expo ──HTTP/JSON──▶ Django REST Framework (/api/v1/) ──▶ PostgreSQL  (one database: users, drivers, riders, staff)
                                        └─ drf-spectacular ─▶ OpenAPI 3 ─▶ Swagger UI (/api/docs/) + ReDoc (/api/redoc/)
Staff browser     ──HTML──────▶ Django views + templates (/dashboard/) ──┘  (same models, same business rules)
```

## Repository layout

```
backend/                     Django 5 + DRF API (no HTML pages for the apps)
  config/                    settings (env-driven), root urls, wsgi
  apps/core/                 base model, error envelope, permissions, OpenAPI helpers, geo, audit log,
                             management commands: seed_demo, dispatch_worker
  apps/accounts/             the single User table, phone OTP login, JWT, devices (push), staff login
  apps/customers/            customer profile, saved places, emergency contacts
  apps/providers/            driver/rider profile, vehicles, documents, online status, GPS
  apps/pricing/              ride types, fare rules (kobo), service zones, fare quotes
  apps/rides/                booking, dispatch (nearest driver, 15 s offers), trip state machine, ratings
  apps/payments/             wallet, cards, promos, provider ledger, payouts, incentives, gateway
  apps/support/              notifications/push, tickets, lost items, SOS, public trip-share link
  apps/staff/                Super Admin business actions (services.py) + optional staff JSON API
  apps/dashboard/            Super Admin web dashboard: Django views, forms, templates, static CSS/JS
  tests/                     end-to-end API + dashboard tests (pytest)
docs/MOBILE_INTEGRATION.md   guide for the Expo developer (auth, client code, per-app endpoints, polling, push)
docs/API_REFERENCE.md        every endpoint: URL, method, auth, body, response, errors + all schemas
docker-compose.yml           Postgres 16 + API + dispatch worker
```

## Run it

```bash
cp backend/.env.example backend/.env
docker compose up --build
docker compose exec api python manage.py seed_demo       # demo data (prints the logins)
```

- Swagger UI: http://localhost:8000/api/docs/ · ReDoc: http://localhost:8000/api/redoc/ · schema: http://localhost:8000/api/schema/
- Health: http://localhost:8000/api/v1/health/

Migration files are included (`backend/apps/*/migrations/0001_initial.py`); the container runs `migrate` on start. When you change a model, run `python manage.py makemigrations` and commit the new file.

- **Super Admin dashboard: http://localhost:8000/dashboard/** — sign in as `admin@chicanocruise.test` / `ChicanoDemo2026!`
  (or `ops@`, `compliance@`, `finance@`, `support@` to see what each role gets).

Without Docker (Python 3.12 + a local Postgres):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgres://chicano:chicano@localhost:5432/chicano   # or sqlite:///db.sqlite3 for a quick look
python manage.py migrate && python manage.py seed_demo
python manage.py runserver 0.0.0.0:8000
python manage.py dispatch_worker --keep-demo-fresh     # second terminal
```

## Tests

```bash
docker compose exec api pytest                          # or: cd backend && pytest
```

17 tests, passing on PostgreSQL 16 and SQLite. Dashboard: sign-in and wrong password, each role sees only its pages (403 otherwise), every page/tab/filter renders, live map JSON and auto-refresh fragments, filters/sorting/CSV export, approving a driver through the HTML forms, private document files (other roles get 403, views are audit-logged), partial refund and the support refund cap, one broadcast per submit, create/edit forms (errors keep input, naira ↔ kobo), manual dispatch (search wider; offline/busy drivers refused), ticket bulk actions (role-checked, audit-logged) and rows-per-page. API: OTP login → estimate → book → offer → accept → arrive → start → complete → collect cash (with the ledger maths), the bike helmet rule, the error envelope, staff document/vehicle/driver approval with roles and audit log, the dashboard, and that the OpenAPI schema builds.

## Key decisions

- **One database, one account per phone.** `accounts.User` + `CustomerProfile` and/or `ProviderProfile` (`service` = car → driver, bike → rider) + staff roles. `GET /auth/me/` returns `roles`.
- **Every endpoint documented** with `extend_schema`: URL + method + auth in the description, request/response serializers, and error responses with example bodies. One error shape everywhere: `{"error": {"code", "message", "details"}}`.
- **Money in kobo** (integers) end to end.
- **Polling, not WebSockets**, so the stack stays HTTP/JSON: customers poll the ride every 3–5 s, drivers/riders poll offers every 3 s; pushes (Expo) nudge the app to refetch.
- **Dispatch**: nearest eligible driver/rider within 5 km gets a 15 s offer; after 3 misses the ride is flagged for Super Admin > Dispatch, where staff can search up to 25 km and assign someone by hand; searches time out after 10 min. `dispatch.tick()` runs on polls and in the `dispatch_worker` process.
- **Bikes**: helmet hand-over must be confirmed before a bike trip can start; bikes can't go online without a passenger helmet.
- **Super Admin dashboard is plain Django**: function-based views, Django forms, templates, session login with CSRF, vanilla CSS/JS (no build step). Live pages (Overview, Dispatch, SOS) re-fetch small template fragments or JSON every 5–10 s; everything else is normal page loads and form posts. The live map uses Leaflet + OpenStreetMap tiles when the browser has internet, and falls back to a plain plot of the same GPS points when it doesn't.
- **Dashboard figures are real queries** (`apps/dashboard/metrics.py`): every card and chart is computed from the database for the chosen period (Today / 7 / 30 days / custom) and compared with the previous period of the same length.
- **One place for business rules**: dashboard views call `apps/staff/services.py` (approve driver, refund, dispatch, payouts, SOS…), which also backs the optional `/api/v1/staff/` JSON API.
- **Staff roles**: super_admin, operations, compliance, finance, support — defined once in `apps/core/roles.py`, enforced on every dashboard view (`@staff_area(...)`) and API endpoint, and used to build the menu. Every staff action is audit-logged.

## Before production

- Set `DJANGO_DEBUG=false`, a real `DJANGO_SECRET_KEY`, `OTP_DEBUG_RETURN_CODE=false`, HTTPS, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` (the dashboard's https origin).
- Run `python manage.py collectstatic`; WhiteNoise serves the dashboard's CSS/JS.
- Plug in real providers where the code has stubs: SMS (`apps/accounts/services.send_sms`), Paystack (`apps/payments/gateway.py`), push is ready for Expo (`PUSH_BACKEND=expo`).
- Driver/rider documents are private: the dashboard streams them through `/dashboard/documents/<id>/file/` (role-checked, audit-logged, `Cache-Control: no-store`). **Don't expose `MEDIA_ROOT/provider_documents/` publicly** — keep media in private object storage and never add a public `/media/` route for it.
- Run `gunicorn config.wsgi:application` and exactly one `dispatch_worker`.

## The Super Admin dashboard

Two panels share one sidebar: **Customers & Rides** (orange accent) and **Drivers & Fleet** (gold accent), plus Platform and Administration.

| Menu | Page | Who (besides super admin) |
|---|---|---|
| Platform | Overview (alerts, KPIs vs previous period, live map), Live map & dispatch, Reports & analytics (CSV export) | everyone / operations / operations + finance |
| Customers & Rides | Customer dashboard, Customers, Rides (filters, CSV), Payments & refunds (records, refunds, failed, disputes), Promo codes, Support tickets & SOS, Lost items | per role — see Staff & roles |
| Drivers & Fleet | Driver dashboard, Drivers & riders, Applications & documents (review queue), Payouts & commission, Incentives | compliance / finance / operations |
| Administration | Broadcasts, All users, Settings (fares, ride types, zones), Staff & roles (permission matrix), Audit log | operations / super admin |

Top bar: breadcrumbs, global search (phone, name, plate, ride ID; press `/` to jump to it), alerts menu (real counts: SOS, unassigned tickets, manual dispatch, documents, failed payouts, failed payments), profile menu with panel switcher. The sidebar collapses to icons on desktop (remembered per browser) and becomes a drawer below 1024 px; tables turn into cards below 768 px and filters fold behind a "More filters" button. Checked at 320, 375, 390, 430, 768, 1024 and 1440 px with no horizontal scrolling.

Every page and action is checked on the server (`@staff_area`), not just hidden in the menu. Refunds above ₦5,000 need finance or super admin. Every staff action goes to the audit log.

## Working on the dashboard (for a Django developer)

| To change… | Edit |
|---|---|
| A page's layout | `apps/dashboard/templates/dashboard/<page>.html` (all extend `base.html`) |
| What a page queries | the view in `apps/dashboard/views/<area>.py` |
| A form (fields, labels, naira ↔ kobo) | `apps/dashboard/forms.py` |
| A business rule (e.g. what's needed to approve a driver) | `apps/staff/services.py` (used by the dashboard and the staff API) |
| Who can see/do what | `apps/core/roles.py` |
| Colours, spacing | `apps/dashboard/static/dashboard/dashboard.css` (tokens at the top) |
| Auto-refresh, dialogs, confirmations | `apps/dashboard/static/dashboard/dashboard.js` + `data-poll`, `data-dialog`, `data-confirm` attributes |
| Template helpers (`naira`, `{% badge %}`, `{% sort_th %}`, `{% query %}`) | `apps/dashboard/templatetags/dashboard_tags.py` |
| Dashboard numbers and charts | `apps/dashboard/metrics.py` |
| Period filter, sorting, CSV, pagination | `apps/dashboard/access.py` (`get_period`, `apply_sort`, `csv_response`, `paginate`) |
| Shared pieces (stat card, empty state, pager, reason dialog) | `apps/dashboard/templates/dashboard/partials/` |

**UI components** (all plain HTML + CSS + vanilla JS; no framework, no build step):

| You write | You get |
|---|---|
| a normal `<select>` (Django renders it) | a styled dropdown: keyboard support, type-to-search when there are more than 8 options, a clear (×) button inside filter bars, a bottom sheet on phones. The real `<select>` stays in the form, so Django validation is unchanged. Add `data-native` to keep a browser select. |
| `<details class="menu row-menu">` + `.menu-panel` with `.menu-item` links/buttons | an animated action menu (⋯) that stays inside the screen, with arrow keys, Escape and click-outside |
| `<form data-confirm="Question? Detail.">` | a styled confirm dialog (never the browser's `confirm()`); `data-confirm-danger` makes it red |
| `<button data-dialog="id">` + `<dialog id="id" class="modal">` | an animated dialog (a bottom sheet on phones); `[data-close]`, Escape or the backdrop closes it |
| `{% include "dashboard/partials/reason_dialog.html" … %}` | a confirm-with-reason dialog for reject/suspend/cancel/refund-style actions |
| `<table class="table responsive">` | a clean table that turns into cards on phones |
| `<table data-bulk="formId">` + `name="ids" form="formId"` checkboxes + `<div class="bulkbar" id="formId-bar">` | select-all and a floating bulk action bar (used on the ticket queue) |
| `{% include "dashboard/partials/pager.html" %}` with `paginate(request, qs)` | page links + "Rows per page" (10/25/50/100, `?per_page=`) |
| `{% include "dashboard/partials/stat.html" with … %}` | a KPI card (same height in a row, optional trend and link) |
| a `BooleanField` in a sectioned form | a toggle switch |
| `data-tip="text"` on any element | a tooltip |

Design tokens (colours, spacing, radius, shadows) are CSS variables at the top of `dashboard.css`. Orange marks Customers & Rides, gold marks Drivers & Fleet; primary buttons are navy everywhere.

Adding a page: write a view decorated with `@staff_area("<area>")`, add it to `apps/dashboard/urls.py`, create a template that `{% extends "dashboard/base.html" %}`, and add a menu entry in `apps/dashboard/context_processors.py` (`NAV`). Use `class="table responsive"` on tables so they become cards on phones.
