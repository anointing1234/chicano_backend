# Chicano Cruise API reference (v1)

Base URL: `http://<host>:8000/api/v1` · Live, always-current version: **Swagger `/api/docs/`**, **ReDoc `/api/redoc/`**, raw OpenAPI 3 `/api/schema/`.
Every endpoint below is documented there with the same URL, method, auth, request body, response body and error responses (with example bodies).

**Auth column:** `—` no token · `Bearer` any logged-in user · `Customer` has a customer profile (User apps) · `Provider` driver or rider (any status) · `Approved` driver/rider with status `approved` · `Staff (roles)` Super Admin with one of those roles (super_admin always allowed).

**Every error** is `{"error": {"code", "message", "details"}}`. The Errors column lists HTTP statuses; the codes are in [Error codes](#error-codes). Bodies are named after the [Schemas](#schemas-request-and-response-bodies) section. Money = integer kobo.

## Auth & account (all four apps)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| POST | `/auth/otp/request/` | — | OtpRequest `{phone}` | 200 OtpRequestResponse | 400 `invalid_phone`, 429 |
| POST | `/auth/otp/verify/` | — | OtpVerify `{phone, code, app}` | 200 AuthResponse `{access, refresh, is_new_user, user}` | 400 `otp_invalid` / `otp_expired` / `otp_locked`, 403 `account_suspended`, 429 |
| POST | `/auth/token/refresh/` | — | Refresh `{refresh}` | 200 TokenPair (new refresh — rotate) | 400, 401 `token_not_valid` |
| POST | `/auth/logout/` | Bearer | Refresh `{refresh}` | 204 | 400, 401 |
| GET | `/auth/me/` | Bearer | — | 200 User (`roles`) | 401 |
| PATCH | `/auth/me/` | Bearer | UserUpdate (JSON or multipart with `photo`) | 200 User | 400, 401 |
| DELETE | `/auth/me/` | Bearer | — | 204 | 401, 409 `active_ride` |
| POST | `/auth/devices/` | Bearer | Device `{app, platform, push_token, app_version}` | 201 Device | 400, 401 |
| DELETE | `/auth/devices/{id}/` | Bearer | — | 204 | 401, 404 |
| GET | `/health/` | — | — | 200 `{status, database}` | — |

## Customer (User app · Cars, User app · Bikes)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET / PATCH | `/customer/profile/` | Customer | CustomerProfile (`data_saver`) | 200 CustomerProfile | 400, 401, 403 |
| GET / POST | `/customer/places/` | Customer | SavedPlace | 200 SavedPlace[] / 201 SavedPlace | 400, 401, 403 |
| GET / PATCH / DELETE | `/customer/places/{id}/` | Customer | SavedPlace (partial) | 200 SavedPlace / 204 | 400, 401, 403, 404 |
| GET / POST | `/customer/emergency-contacts/` | Customer | EmergencyContact (max 5) | 200 EmergencyContact[] / 201 | 400 `limit_reached`, 401, 403 |
| GET / PATCH / DELETE | `/customer/emergency-contacts/{id}/` | Customer | EmergencyContact (partial) | 200 / 204 | 400, 401, 403, 404 |

## Booking (User apps)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/ride-types/?service=car\|bike` | Bearer | — | 200 RideType[] | 401 |
| POST | `/rides/estimate/` | Customer | EstimateRequest `{service, pickup, dropoff, stops?, promo_code?}` | 200 FareQuote[] (one per ride type, valid 10 min) | 400 `outside_service_zone` / `promo_*`, 401, 403, 409 `pricing_unavailable` |
| POST | `/rides/` | Customer | RideRequest `{quote_id, payment_method, payment_method_id?, pickup_address, dropoff_address, pickup_note?, stop_addresses?, scheduled_for?}` | 201 Ride | 400 `card_required`, 401, 402 `insufficient_wallet`, 403, 404 `quote_not_found` / `card_not_found`, 409 `quote_expired` / `quote_used` / `active_ride_exists` |
| GET | `/rides/?status=&service=&page=` | Customer | — | 200 paginated RideList | 401, 403 |
| GET | `/rides/active/` | Customer | — | 200 Ride | 401, 403, 404 `no_active_ride` |
| GET | `/rides/{id}/` | Customer | — | 200 Ride (poll 3–5 s) | 401, 403, 404 |
| POST | `/rides/{id}/cancel/` | Customer | Cancel `{reason?}` | 200 Ride | 400, 401, 403, 404, 409 `invalid_state` |
| POST | `/rides/{id}/rate/` | Customer | Rate `{stars, tags?, comment?, tip_amount?}` | 201 Rating | 400, 401, 402, 403, 404, 409 `already_rated` / `invalid_state` |
| POST | `/rides/{id}/tip/` | Customer | Tip `{amount}` | 200 Ride | 400, 401, 402 `payment_failed`, 403, 404, 409 `already_tipped` / `cash_already_collected` |
| POST | `/rides/{id}/retry-payment/` | Customer | RetryPayment `{payment_method, payment_method_id?}` | 200 Ride | 400, 401, 402, 403, 404, 409 `payment_not_failed` |
| POST | `/rides/{id}/sos/` | Customer | SOSRequest `{lat?, lng?}` | 201 SOSAlert | 401, 403, 404 |

## Wallet & payments (User apps)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/wallet/` | Customer | — | 200 Wallet | 401, 403 |
| GET | `/wallet/transactions/` | Customer | — | 200 paginated WalletTransaction | 401, 403 |
| POST | `/wallet/top-up/` | Customer | TopUp `{amount, payment_method_id}` | 200 Wallet | 400, 401, 402 `payment_failed`, 403, 404 |
| GET / POST | `/wallet/payment-methods/` | Customer | AddCard `{gateway_token, brand, last4, exp_month, exp_year, make_default?}` | 200 PaymentMethod[] / 201 PaymentMethod | 400, 401, 403 |
| DELETE | `/wallet/payment-methods/{id}/` | Customer | — | 204 | 401, 403, 404 |
| POST | `/wallet/promos/validate/` | Customer | PromoValidate `{code, service}` | 200 PromoPublic | 400 `promo_invalid` / `promo_expired` / `promo_not_started` / `promo_used` / `promo_wrong_service` / `promo_exhausted` / `promo_first_ride_only`, 401, 403 |

## Provider (Driver app · Cars, Rider app · Bikes)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| POST | `/provider/apply/` | Bearer | Apply `{service, city, first_name?, last_name?}` | 201 ProviderProfile | 400, 401, 409 `already_provider` |
| GET | `/provider/onboarding/` | Provider | — | 200 Onboarding (`steps[]`) | 401, 403 |
| GET / PATCH | `/provider/me/` | Provider | ProviderProfile (bank details, date_of_birth, city) | 200 ProviderProfile | 400, 401, 403 |
| GET / POST | `/provider/vehicles/` | Provider | Vehicle | 200 Vehicle[] / 201 Vehicle | 400 `wrong_vehicle_kind`, 401, 403 |
| GET | `/provider/documents/` | Provider | — | 200 Document[] | 401, 403 |
| POST | `/provider/documents/` | Provider | **multipart** Document `{doc_type, file, back_file?, number?, expires_at?}` | 201 Document | 400, 401, 403 |
| POST | `/provider/status/` | Provider | Status `{is_online}` | 200 ProviderProfile | 400, 401, 403 `provider_not_approved`, 409 `no_approved_vehicle` / `helmet_required` / `document_expired` / `active_ride` |
| POST | `/provider/location/` | Provider | Location `{lat, lng, heading?}` | 204 | 400, 401, 403, 429 (limit 120/min) |

## Provider trips

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/provider/offers/current/` | Approved | — | 200 Offer · 204 none (poll 3 s) | 401, 403 |
| POST | `/provider/offers/{id}/accept/` | Approved | — | 200 ProviderTrip | 401, 403, 404, 409 `offer_expired` / `already_on_trip` |
| POST | `/provider/offers/{id}/decline/` | Approved | — | 204 | 401, 403, 404, 409 |
| GET | `/provider/trips/current/` | Approved | — | 200 ProviderTrip · 204 none (poll 5 s) | 401, 403 |
| GET | `/provider/trips/?page=` | Approved | — | 200 paginated ProviderTrip | 401, 403 |
| POST | `/provider/trips/{id}/arrive/` | Approved | — | 200 ProviderTrip | 401, 403, 404, 409 `invalid_state` |
| POST | `/provider/trips/{id}/helmet-check/` | Approved (bikes) | — | 200 ProviderTrip | 401, 403, 404, 409 `not_bike_ride` / `invalid_state` |
| POST | `/provider/trips/{id}/start/` | Approved | — | 200 ProviderTrip | 401, 403, 404, 409 `helmet_check_required` / `invalid_state` |
| POST | `/provider/trips/{id}/complete/` | Approved | — | 200 ProviderTrip (`cash_due_amount`) | 401, 403, 404, 409 |
| POST | `/provider/trips/{id}/collect-cash/` | Approved | — | 200 ProviderTrip | 401, 403, 404, 409 `not_cash_ride` / `invalid_state` |
| POST | `/provider/trips/{id}/helmet-returned/` | Approved (bikes) | — | 200 ProviderTrip | 401, 403, 404, 409 `not_bike_ride` |
| POST | `/provider/trips/{id}/cancel/` | Approved | Cancel `{reason?}` | 200 ProviderTrip | 400, 401, 403, 404, 409 |
| POST | `/provider/trips/{id}/no-show/` | Approved | — | 200 ProviderTrip | 401, 403, 404, 409 `no_show_too_early` |
| POST | `/provider/trips/{id}/rate/` | Approved | Rate `{stars, tags?, comment?}` | 201 Rating | 400, 401, 403, 404, 409 `already_rated` |
| POST | `/provider/sos/` | Approved | SOSRequest `{lat?, lng?}` | 201 SOSAlert | 400, 401, 403 |

## Provider earnings

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/provider/earnings/summary/?period=day\|week\|month` | Provider | — | 200 EarningsSummary | 400, 401, 403 |
| GET | `/provider/earnings/ledger/` | Provider | — | 200 paginated LedgerEntry | 401, 403 |
| GET | `/provider/payouts/` | Provider | — | 200 Payout[] | 401, 403 |
| POST | `/provider/payouts/instant/` | Approved | — | 201 Payout | 401, 403, 409 `balance_too_low` / `bank_details_missing` |
| GET | `/provider/incentives/` | Provider | — | 200 IncentiveProgress[] | 401, 403 |

## Support & safety (all apps)

| Method | URL | Auth | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/support/notifications/` | Bearer | — | 200 paginated Notification | 401 |
| POST | `/support/notifications/read-all/` | Bearer | — | 204 | 401 |
| GET / POST | `/support/tickets/` | Bearer | TicketCreate `{ride?, category, subject, message}` | 200 Ticket[] / 201 Ticket | 400, 401 |
| GET | `/support/tickets/{id}/` | Bearer (owner) | — | 200 Ticket | 401, 404 |
| POST | `/support/tickets/{id}/reply/` | Bearer (owner) | TicketReply `{body}` | 200 Ticket | 400, 401, 404 |
| GET / POST | `/support/lost-items/` | Customer | LostItem `{ride, category, description, contact_phone}` | 200 LostItem[] / 201 LostItem | 400, 401, 403, 404 |
| POST | `/support/sos/` | Bearer | SOSRequest | 201 SOSAlert | 400, 401 |
| GET | `/support/share/{token}/` | — (public) | — | 200 PublicTrip | 404 |

## Staff JSON API (`/staff/...`, optional)

The Super Admin **web dashboard** is server-rendered Django at `/dashboard/` and doesn't use these endpoints (it calls `apps/staff/services.py` directly). This JSON API exposes the same actions for scripts or a future staff app. The mobile apps never call it.

All need a staff token from `POST /staff/auth/login/ {email, password}` (→ 200 `{access, refresh, user}`; 400 `invalid_credentials`, 403 `account_suspended`). Lists are paginated and most accept `?service=all|car|bike`. Every write is recorded in the audit log.

| Method | URL | Roles | Body | Response |
|---|---|---|---|---|
| GET | `/staff/me/` | any staff | — | StaffUser |
| GET | `/staff/overview/?service=` | any staff | — | Overview (today's numbers + 7-day series) |
| GET | `/staff/live-map/?service=` | any staff | — | LiveProvider[] |
| GET | `/staff/users/?role=&status=&search=` | operations, support | — | StaffUserRow (paginated) |
| GET | `/staff/users/{id}/` | operations, support | — | StaffUserDetail |
| POST | `/staff/users/{id}/suspend/` · `/ban/` | operations | `{reason}` | StaffUserDetail (409 `cannot_change_self`) |
| POST | `/staff/users/{id}/reinstate/` | operations | — | StaffUserDetail |
| GET | `/staff/providers/?service=&status=&online=&search=` | operations, compliance, support | — | StaffProviderRow (paginated) |
| GET | `/staff/providers/{id}/` | operations, compliance, support | — | StaffProviderDetail (vehicles, documents, balance, checklist) |
| POST | `/staff/providers/{id}/approve/` | compliance | — | StaffProviderDetail (409 `not_ready` with `missing_documents`, `vehicle_approved`) |
| POST | `/staff/providers/{id}/reject/` | compliance | `{reason}` | StaffProviderDetail |
| POST | `/staff/providers/{id}/suspend/` | operations, compliance | `{reason}` | StaffProviderDetail (409 `active_ride_exists`) |
| POST | `/staff/providers/{id}/reinstate/` | operations, compliance | — | StaffProviderDetail |
| POST | `/staff/vehicles/{id}/approve/` | compliance | `{ride_types: ["car_standard", ...]}` | Vehicle (400 if year too old / helmets missing) |
| POST | `/staff/vehicles/{id}/reject/` | compliance | `{reason}` | Vehicle |
| GET | `/staff/documents/?status=pending&service=&doc_type=&provider=` | compliance | — | StaffDocument (paginated) |
| POST | `/staff/documents/{id}/approve/` | compliance | — | StaffDocument (409 `document_expired`) |
| POST | `/staff/documents/{id}/reject/` | compliance | `{reason}` | StaffDocument |
| GET | `/staff/rides/?service=&status=&payment_method=&date_from=&date_to=&customer=&provider=&search=` | operations, support, finance | — | StaffRideRow (paginated) |
| GET | `/staff/rides/{id}/` | operations, support, finance | — | StaffRideDetail (events, offers, ratings) |
| POST | `/staff/rides/{id}/refund/` | finance, support (≤ ₦5,000) | `{amount, reason, claw_back}` | StaffRideDetail (400 `invalid_amount`, 403 `refund_limit`, 409) |
| POST | `/staff/rides/{id}/cancel/` | operations | `{reason}` | StaffRideDetail (409 once in progress) |
| GET | `/staff/dispatch/?service=` | operations | — | StaffRideRow[] (searching; manual first) |
| GET | `/staff/dispatch/{ride_id}/candidates/` | operations | — | Candidate[] |
| POST | `/staff/dispatch/{ride_id}/assign/` | operations | `{provider_id}` | 201 StaffOffer (400 `provider_not_eligible`, 409) |
| GET | `/staff/payouts/?status=&service=&method=` | finance | — | StaffPayout (paginated) |
| POST | `/staff/payouts/run/` | finance | — | 201 `{created, total_amount, payouts}` |
| POST | `/staff/payouts/{id}/mark-paid/` | finance | `{reference?}` | StaffPayout (409 if already final) |
| POST | `/staff/payouts/{id}/mark-failed/` | finance | `{reason}` | StaffPayout |
| CRUD | `/staff/promotions/` · `/{id}/` | operations, finance | PromoAdmin | PromoAdmin (DELETE 409 `in_use`) |
| CRUD | `/staff/incentives/` · `/{id}/` | operations, finance | IncentiveAdmin | IncentiveAdmin |
| CRUD | `/staff/ride-types/` · `/{id}/` | read: any staff · write: super_admin | RideType | RideType |
| CRUD | `/staff/fare-rules/` · `/{id}/` | read: any staff · write: super_admin | FareRule (saving an active rule deactivates the old one) | FareRule |
| CRUD | `/staff/service-zones/` · `/{id}/` | read: any staff · write: super_admin | ServiceZone | ServiceZone |
| GET / POST / PATCH | `/staff/team/` · `/{id}/` | super_admin | StaffMember (`password` write-only) | StaffMember |
| GET | `/staff/tickets/?status=&category=&priority=&assigned=me\|none` | support, operations | — | StaffTicket (paginated, urgent first) |
| GET / PATCH | `/staff/tickets/{id}/` | support, operations | `{status?, priority?, assigned_to?}` | StaffTicket (incl. internal notes) |
| POST | `/staff/tickets/{id}/reply/` | support, operations | `{body, internal, status?}` | StaffTicket |
| GET | `/staff/sos/?status=active\|open\|acknowledged\|resolved\|all&service=` | support, operations | — | StaffSOS (paginated) |
| POST | `/staff/sos/{id}/acknowledge/` · `/resolve/` | support, operations | `{notes?}` | StaffSOS |
| GET | `/staff/audit-log/?action=&actor=&target_type=&target_id=` | super_admin | — | AuditLog (paginated) |

## Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 400 | Body/query invalid; `details` = field → messages |
| `invalid_phone` | 400 | Not a valid Nigerian number |
| `otp_invalid` / `otp_expired` / `otp_locked` | 400 | Wrong code (`details.attempts_remaining`) / expired / too many attempts |
| `invalid_credentials` | 400 | Staff login failed |
| `not_authenticated` / `token_not_valid` | 401 | Refresh, then log in again |
| `permission_denied` | 403 | Wrong role or not your record |
| `account_suspended` | 403 | Account on hold (`details.status`) |
| `provider_not_approved` | 403 | Driver/rider not approved yet (`details.status`) |
| `refund_limit` | 403 | Support staff refund above ₦5,000 |
| `not_found`, `no_active_ride`, `quote_not_found`, `card_not_found` | 404 | Missing record |
| `insufficient_wallet` | 402 | `details.balance_amount`, `details.required_amount` |
| `payment_failed` | 402 | Card declined |
| `card_required` | 400 | `payment_method=card` without a saved card |
| `outside_service_zone` | 400 | Pickup/drop-off outside where the service runs |
| `pricing_unavailable` | 409 | No active fare rule for that ride type/city |
| `promo_invalid`, `promo_expired`, `promo_not_started`, `promo_used`, `promo_wrong_service`, `promo_exhausted`, `promo_first_ride_only` | 400 | Promo can't be used |
| `quote_expired` / `quote_used` | 409 | Get a new estimate |
| `active_ride_exists` | 409 | Customer already has an active ride (or provider on a trip, staff side) |
| `invalid_state` | 409 | Action not allowed in the current status (`details.status`) |
| `offer_expired` | 409 | The 15 s window passed or the ride was taken/cancelled |
| `already_on_trip` | 409 | Provider must finish the current trip first |
| `helmet_check_required` | 409 | Bike trip started before the helmet hand-over |
| `helmet_required` | 409 | Bike has no passenger helmet recorded (going online) |
| `no_approved_vehicle` | 409 | Going online without an approved active vehicle |
| `document_expired` | 409 | A licence/insurance expired (`details.doc_types`) |
| `active_ride` | 409 | Going offline / deleting account mid-trip |
| `no_show_too_early` | 409 | Free waiting time hasn't passed yet |
| `not_bike_ride` / `not_cash_ride` | 409 | Action doesn't apply to this trip |
| `already_rated` / `already_tipped` / `cash_already_collected` / `payment_not_failed` | 409 | Already done |
| `already_provider` | 409 | Account is already a driver/rider of the other service |
| `wrong_vehicle_kind` | 400 | Car added to a rider account or vice versa |
| `limit_reached` | 400 | E.g. more than 5 emergency contacts |
| `balance_too_low` / `bank_details_missing` | 409 | Instant payout not possible |
| `invalid_amount` | 400 | Refund amount out of range (`details.max_amount`) |
| `not_ready` | 409 | Staff approval before documents/vehicle are approved |
| `provider_not_eligible` | 400 | Staff manual assignment to someone who can't take the ride (wrong service, not approved, offline, already on a trip, or no approved vehicle for this ride type) |
| `cannot_change_self` | 409 | Staff changing their own status/role |
| `in_use` | 409 | Delete blocked by existing trips; deactivate instead |
| `throttled` | 429 | `details.retry_after_seconds` |

## Schemas (request and response bodies)

Generated from `apps/*/serializers.py`. Field types: `integer` amounts ending in `_amount` are **kobo**; `decimal string` is e.g. `"6.447400"`; `datetime` is ISO 8601 with offset. Swagger (`/api/docs/`) shows the same schemas with required/optional flags and examples.


### accounts


#### OtpRequest

| Field | Type | Notes |
|---|---|---|
| `phone` | string | Nigerian number in any common format: 08034125567, +2348034125567. |

#### OtpRequestResponse

| Field | Type | Notes |
|---|---|---|
| `phone` | string | Normalised E.164 number. Send this exact value to /otp/verify/. |
| `expires_in_seconds` | integer |  |
| `resend_after_seconds` | integer |  |
| `debug_code` | string | optional; DEV ONLY (OTP_DEBUG_RETURN_CODE=true). Never present in production. |

#### OtpVerify

| Field | Type | Notes |
|---|---|---|
| `phone` | string |  |
| `code` | string | 6-digit SMS code. |
| `app` | enum | Which app is logging in. user_cars / user_bikes create a customer profile on first login. driver_cars / rider_bikes do not create anything: call POST /provider/apply/ next if `roles` has no driver/rider. |

#### User

The logged-in user (also nested elsewhere as the public-safe subset).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `phone` | string |  |
| `email` | string | nullable |
| `first_name` | string |  |
| `last_name` | string |  |
| `full_name` | string | read-only |
| `photo` | image URL | nullable |
| `status` | string |  |
| `roles` | list | read-only; Any of "customer", "driver", "rider", "staff". |
| `phone_verified_at` | datetime | nullable |
| `date_joined` | datetime |  |

#### UserUpdate

PATCH /auth/me/ accepts these fields. Use multipart to upload `photo`.

| Field | Type | Notes |
|---|---|---|
| `first_name` | string |  |
| `last_name` | string |  |
| `email` | string | nullable |
| `photo` | image URL | nullable |

#### AuthResponse

| Field | Type | Notes |
|---|---|---|
| `access` | string | Short-lived (30 min). Send as `Authorization: Bearer <access>`. |
| `refresh` | string | Long-lived (30 days). Store in expo-secure-store; use to get new access tokens. |
| `is_new_user` | boolean | True on first login: show the profile-setup screen. |
| `user` | User |  |

#### Refresh

| Field | Type | Notes |
|---|---|---|
| `refresh` | string |  |

#### TokenPair

| Field | Type | Notes |
|---|---|---|
| `access` | string |  |
| `refresh` | string | A NEW refresh token (rotation). Replace the stored one. |

#### Device

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `app` | string |  |
| `platform` | string |  |
| `push_token` | string |  |
| `app_version` | string |  |
| `last_seen_at` | datetime |  |

#### StaffLogin

| Field | Type | Notes |
|---|---|---|
| `email` | string |  |
| `password` | string | write-only |

#### StaffAuthResponse

| Field | Type | Notes |
|---|---|---|
| `access` | string |  |
| `refresh` | string |  |
| `user` | StaffUser |  |

### customers


#### CustomerProfile

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `user` | User | read-only |
| `rating_avg` | decimal string |  |
| `rating_count` | integer |  |
| `total_trips` | integer |  |
| `referral_code` | string |  |
| `data_saver` | boolean |  |

#### SavedPlace

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `label` | string |  |
| `name` | string |  |
| `address` | string |  |
| `lat` | decimal string |  |
| `lng` | decimal string |  |
| `place_id` | string |  |
| `note` | string |  |
| `created_at` | datetime |  |

#### EmergencyContact

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `name` | string |  |
| `phone` | string |  |
| `relationship` | string |  |

### payments


#### Wallet

| Field | Type | Notes |
|---|---|---|
| `balance_amount` | integer |  |
| `currency` | string |  |
| `updated_at` | datetime |  |

#### WalletTransaction

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `kind` | string |  |
| `amount` | integer |  |
| `balance_after` | integer |  |
| `ride` | uuid | nullable |
| `note` | string |  |
| `created_at` | datetime |  |

#### PaymentMethod

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `brand` | string |  |
| `last4` | string |  |
| `exp_month` | integer |  |
| `exp_year` | integer |  |
| `is_default` | boolean |  |
| `created_at` | datetime |  |

#### AddCard

The app tokenises the card with the gateway SDK first. Never send card numbers to this API.

| Field | Type | Notes |
|---|---|---|
| `gateway_token` | string | Paystack authorization_code (or 'fail_...' to simulate a declining card in dev). |
| `brand` | string |  |
| `last4` | string |  |
| `exp_month` | integer |  |
| `exp_year` | integer |  |
| `make_default` | boolean |  |

#### TopUp

| Field | Type | Notes |
|---|---|---|
| `amount` | integer | kobo (₦100 – ₦500,000) |
| `payment_method_id` | uuid | Saved card to charge. |

#### PromoValidate

| Field | Type | Notes |
|---|---|---|
| `code` | string |  |
| `service` | enum | car / bike |

#### PromoPublic

| Field | Type | Notes |
|---|---|---|
| `code` | string |  |
| `description` | string |  |
| `discount_type` | string |  |
| `value` | integer |  |
| `max_discount_amount` | integer | nullable |
| `service` | string | nullable |
| `valid_to` | datetime |  |

#### PromoAdmin

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `code` | string |  |
| `description` | string |  |
| `discount_type` | string |  |
| `value` | integer |  |
| `max_discount_amount` | integer | nullable |
| `service` | string | nullable |
| `usage_limit` | integer | nullable |
| `per_user_limit` | integer |  |
| `first_ride_only` | boolean |  |
| `budget_amount` | integer | nullable |
| `spent_amount` | integer |  |
| `valid_from` | datetime |  |
| `valid_to` | datetime |  |
| `is_active` | boolean |  |
| `redemptions_count` | integer | read-only; optional |
| `created_at` | datetime |  |

#### LedgerEntry

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `kind` | string |  |
| `amount` | integer |  |
| `ride` | uuid | nullable |
| `payout` | uuid | nullable |
| `note` | string |  |
| `created_at` | datetime |  |

#### EarningsDay

| Field | Type | Notes |
|---|---|---|
| `date` | date |  |
| `trips` | integer |  |
| `gross_amount` | integer |  |

#### EarningsSummary

| Field | Type | Notes |
|---|---|---|
| `period` | enum | day / week / month |
| `starts_at` | datetime |  |
| `trips` | integer |  |
| `fares_amount` | integer |  |
| `tips_amount` | integer |  |
| `incentives_amount` | integer |  |
| `cancellation_fees_amount` | integer |  |
| `commission_amount` | integer |  |
| `net_amount` | integer | What you earned in the period. |
| `cash_collected_amount` | integer | Cash you already hold from customers. |
| `balance_amount` | integer | What Chicano Cruise owes you now (negative = you owe commission on cash trips). |
| `by_day` | list of EarningsDay |  |

#### Payout

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `amount` | integer |  |
| `fee_amount` | integer |  |
| `method` | string |  |
| `status` | string |  |
| `bank_name` | string |  |
| `bank_account_number` | string |  |
| `bank_account_name` | string |  |
| `reference` | string |  |
| `failure_reason` | string |  |
| `paid_at` | datetime | nullable |
| `created_at` | datetime |  |

#### IncentiveProgress

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `name` | string |  |
| `description` | string |  |
| `target_trips` | integer |  |
| `completed_trips` | integer |  |
| `reward_amount` | integer |  |
| `starts_at` | datetime |  |
| `ends_at` | datetime |  |
| `achieved` | boolean |  |
| `status` | enum | active / achieved / ended |

#### IncentiveAdmin

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `service` | string |  |
| `name` | string |  |
| `description` | string |  |
| `target_trips` | integer |  |
| `reward_amount` | integer |  |
| `starts_at` | datetime |  |
| `ends_at` | datetime |  |
| `min_acceptance_rate` | integer |  |
| `is_active` | boolean |  |

### pricing


#### RideType

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `code` | string |  |
| `service` | string |  |
| `name` | string |  |
| `description` | string |  |
| `seats` | integer |  |
| `min_vehicle_year` | integer | nullable |
| `is_active` | boolean |  |
| `sort_order` | integer |  |

#### FareRule

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `ride_type` | uuid |  |
| `ride_type_code` | string | read-only |
| `city` | string |  |
| `base_amount` | integer |  |
| `per_km_amount` | integer |  |
| `per_min_amount` | integer |  |
| `minimum_amount` | integer |  |
| `booking_fee_amount` | integer |  |
| `cancellation_fee_amount` | integer |  |
| `cancellation_grace_seconds` | integer |  |
| `free_wait_seconds` | integer |  |
| `wait_per_min_amount` | integer |  |
| `commission_percent` | decimal string |  |
| `is_active` | boolean |  |

#### ServiceZone

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `service` | string |  |
| `name` | string |  |
| `city` | string |  |
| `min_lat` | decimal string |  |
| `max_lat` | decimal string |  |
| `min_lng` | decimal string |  |
| `max_lng` | decimal string |  |
| `is_active` | boolean |  |

#### FareQuote

One priced option in the 'Choose a ride' sheet.

| Field | Type | Notes |
|---|---|---|
| `quote_id` | uuid | read-only; Send this to POST /rides/ to book at this price. |
| `ride_type` | RideType | read-only |
| `distance_m` | integer |  |
| `duration_s` | integer |  |
| `gross_amount` | integer |  |
| `discount_amount` | integer |  |
| `total_amount` | integer |  |
| `breakdown` | object |  |
| `promo_code` | string | read-only |
| `expires_at` | datetime |  |

### providers


#### Vehicle

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `kind` | string |  |
| `make` | string |  |
| `model` | string |  |
| `year` | integer |  |
| `color` | string |  |
| `plate_number` | string |  |
| `seats` | integer |  |
| `ride_types` | list of list of codes | read-only |
| `has_rider_helmet` | boolean |  |
| `has_passenger_helmet` | boolean |  |
| `has_reflective_vest` | boolean |  |
| `status` | string |  |
| `is_active` | boolean |  |
| `created_at` | datetime |  |

#### VehiclePublic

What a customer sees about the vehicle on their trip.

| Field | Type | Notes |
|---|---|---|
| `kind` | string |  |
| `make` | string |  |
| `model` | string |  |
| `color` | string |  |
| `plate_number` | string |  |
| `seats` | integer |  |

#### ProviderProfile

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `user` | User | read-only |
| `service` | string |  |
| `kind` | string | read-only; "driver" (cars) or "rider" (bikes). |
| `status` | string |  |
| `status_reason` | string |  |
| `city` | string |  |
| `date_of_birth` | date | nullable |
| `is_online` | boolean |  |
| `rating_avg` | decimal string |  |
| `rating_count` | integer |  |
| `total_trips` | integer |  |
| `acceptance_rate` | number | read-only |
| `cancellation_rate` | number | read-only |
| `bank_name` | string |  |
| `bank_account_number` | string |  |
| `bank_account_name` | string |  |
| `active_vehicle` | computed |  |
| `approved_at` | datetime | nullable |

#### ProviderPublic

What a customer sees about their driver/rider (no bank details, no phone: calls are masked).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `first_name` | string |  |
| `last_initial` | computed |  |
| `photo` | image URL | read-only |
| `kind` | string | read-only |
| `rating_avg` | decimal string |  |
| `total_trips` | integer |  |

#### Apply

| Field | Type | Notes |
|---|---|---|
| `service` | enum | car = Driver app · Cars, bike = Rider app · Bikes |
| `city` | string |  |
| `first_name` | string | optional |
| `last_name` | string | optional |

#### Document

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `doc_type` | string |  |
| `file` | file URL |  |
| `back_file` | file URL | nullable |
| `number` | string |  |
| `expires_at` | date | nullable |
| `status` | string |  |
| `rejection_reason` | string |  |
| `reviewed_at` | datetime | nullable |
| `created_at` | datetime |  |

#### OnboardingStep

| Field | Type | Notes |
|---|---|---|
| `key` | string |  |
| `title` | string |  |
| `state` | enum | done / in_review / action_needed / todo |
| `documents` | list | optional |

#### Onboarding

| Field | Type | Notes |
|---|---|---|
| `status` | string |  |
| `service` | string |  |
| `kind` | string |  |
| `steps_done` | integer |  |
| `steps_total` | integer |  |
| `steps` | list of OnboardingStep |  |
| `can_go_online` | boolean |  |

#### Status

| Field | Type | Notes |
|---|---|---|
| `is_online` | boolean |  |

#### Location

| Field | Type | Notes |
|---|---|---|
| `lat` | decimal string |  |
| `lng` | decimal string |  |
| `heading` | integer | optional |

### rides


#### Point

| Field | Type | Notes |
|---|---|---|
| `lat` | decimal string |  |
| `lng` | decimal string |  |

#### StopInput

| Field | Type | Notes |
|---|---|---|
| `address` | string |  |

#### EstimateRequest

| Field | Type | Notes |
|---|---|---|
| `service` | enum | car / bike |
| `pickup` | Point |  |
| `dropoff` | Point |  |
| `stops` | list of StopInput | optional; Cars only, max 3. |
| `promo_code` | string | optional |

#### RideRequest

| Field | Type | Notes |
|---|---|---|
| `quote_id` | uuid | From POST /rides/estimate/ (valid 10 minutes). |
| `payment_method` | enum |  |
| `payment_method_id` | uuid | optional; Saved card id when payment_method=card. |
| `pickup_address` | string |  |
| `dropoff_address` | string |  |
| `pickup_note` | string | optional; Landmark for the driver, e.g. 'Blue gate, opposite GTBank'. |
| `stop_addresses` | list | optional |
| `scheduled_for` | datetime | optional; Cars only. ISO 8601, 30 min to 7 days ahead. |

#### RideStop

| Field | Type | Notes |
|---|---|---|
| `order` | integer |  |
| `lat` | decimal string |  |
| `lng` | decimal string |  |
| `address` | string |  |
| `completed_at` | datetime | nullable |

#### ProviderLocation

| Field | Type | Notes |
|---|---|---|
| `lat` | decimal string |  |
| `lng` | decimal string |  |
| `heading` | integer |  |
| `updated_at` | datetime |  |

#### Ride

Customer view of a ride. Poll GET /rides/{id}/ every 3-5 s while `is_active` is true.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `service` | string |  |
| `status` | string |  |
| `is_active` | boolean | read-only |
| `ride_type` | RideType | read-only |
| `pickup_lat` | decimal string |  |
| `pickup_lng` | decimal string |  |
| `pickup_address` | string |  |
| `pickup_note` | string |  |
| `dropoff_lat` | decimal string |  |
| `dropoff_lng` | decimal string |  |
| `dropoff_address` | string |  |
| `stops` | list of RideStop | read-only |
| `scheduled_for` | datetime | nullable |
| `distance_m` | integer |  |
| `duration_s` | integer |  |
| `gross_amount` | integer |  |
| `discount_amount` | integer |  |
| `wait_charge_amount` | integer |  |
| `total_amount` | integer |  |
| `tip_amount` | integer |  |
| `cancellation_fee_amount` | integer |  |
| `refunded_amount` | integer |  |
| `payment_method` | string |  |
| `payment_status` | string |  |
| `provider` | ProviderPublic | read-only; Driver (car) or rider (bike). Null while searching. |
| `vehicle` | VehiclePublic | read-only |
| `provider_location` | computed | Live position while accepted/arrived/in_progress. |
| `eta_to_pickup_seconds` | computed |  |
| `requires_helmet` | computed | True for bike rides: show the helmet reminder. |
| `cancellation_fee_if_cancelled_now` | computed |  |
| `share_url` | computed |  |
| `my_rating` | computed |  |
| `requested_at` | datetime |  |
| `accepted_at` | datetime | nullable |
| `arrived_at` | datetime | nullable |
| `started_at` | datetime | nullable |
| `completed_at` | datetime | nullable |
| `cancelled_at` | datetime | nullable |
| `cancelled_by` | string |  |
| `cancel_reason` | string |  |

#### RideList

Compact row for the Rides tab.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `service` | string |  |
| `status` | string |  |
| `ride_type_name` | string |  |
| `pickup_address` | string |  |
| `dropoff_address` | string |  |
| `scheduled_for` | datetime | nullable |
| `total_amount` | integer |  |
| `tip_amount` | integer |  |
| `payment_method` | string |  |
| `payment_status` | string |  |
| `requested_at` | datetime |  |
| `completed_at` | datetime | nullable |

#### Cancel

| Field | Type | Notes |
|---|---|---|
| `reason` | string | optional |

#### Rate

| Field | Type | Notes |
|---|---|---|
| `stars` | integer |  |
| `tags` | list | optional |
| `comment` | string | optional |
| `tip_amount` | integer | optional; kobo. Customer only. |

#### Tip

| Field | Type | Notes |
|---|---|---|
| `amount` | integer | kobo (e.g. 50000 = ₦500) |

#### RetryPayment

| Field | Type | Notes |
|---|---|---|
| `payment_method` | enum |  |
| `payment_method_id` | uuid | optional |

#### Rating

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `direction` | string |  |
| `stars` | integer |  |
| `tags` | object |  |
| `comment` | string |  |
| `created_at` | datetime |  |

#### CustomerPublic

| Field | Type | Notes |
|---|---|---|
| `first_name` | string |  |
| `rating_avg` | decimal string |  |
| `total_trips` | integer |  |

#### Offer

The incoming-request card. Show a countdown from `seconds_left`.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `ride` | uuid |  |
| `status` | string |  |
| `is_manual` | boolean |  |
| `expires_at` | datetime |  |
| `seconds_left` | computed |  |
| `service` | string |  |
| `ride_type_name` | string |  |
| `distance_to_pickup_m` | integer |  |
| `eta_to_pickup_s` | integer |  |
| `pickup_address` | string |  |
| `pickup_note` | string |  |
| `pickup_lat` | decimal string |  |
| `pickup_lng` | decimal string |  |
| `dropoff_address` | string |  |
| `trip_distance_m` | integer |  |
| `trip_duration_s` | integer |  |
| `estimated_fare_amount` | integer | What the customer pays (kobo). |
| `payment_method` | string |  |
| `stops_count` | computed |  |
| `customer` | computed |  |

#### ProviderTrip

Driver/rider view of their current or past trip.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `service` | string |  |
| `status` | string |  |
| `ride_type_name` | string |  |
| `pickup_lat` | decimal string |  |
| `pickup_lng` | decimal string |  |
| `pickup_address` | string |  |
| `pickup_note` | string |  |
| `dropoff_lat` | decimal string |  |
| `dropoff_lng` | decimal string |  |
| `dropoff_address` | string |  |
| `stops` | list of RideStop | read-only |
| `distance_m` | integer |  |
| `duration_s` | integer |  |
| `gross_amount` | integer |  |
| `discount_amount` | integer |  |
| `wait_charge_amount` | integer |  |
| `total_amount` | integer |  |
| `tip_amount` | integer |  |
| `commission_amount` | integer |  |
| `payment_method` | string |  |
| `payment_status` | string |  |
| `cash_due_amount` | integer | read-only; Cash to collect (0 for card/wallet). Includes tip. |
| `cash_collected_at` | datetime | nullable |
| `customer` | computed |  |
| `helmet_required` | computed |  |
| `helmet_handed_over_at` | datetime | nullable |
| `helmet_returned_at` | datetime | nullable |
| `accepted_at` | datetime | nullable |
| `arrived_at` | datetime | nullable |
| `started_at` | datetime | nullable |
| `completed_at` | datetime | nullable |
| `cancelled_at` | datetime | nullable |
| `cancel_reason` | string |  |

#### RideEvent

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `event` | string |  |
| `actor_name` | string |  |
| `data` | object |  |
| `created_at` | datetime |  |

### support


#### Notification

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `title` | string |  |
| `body` | string |  |
| `data` | object |  |
| `is_read` | computed |  |
| `read_at` | datetime | nullable |
| `created_at` | datetime |  |

#### TicketMessage

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `sender_name` | string | read-only |
| `from_staff` | computed |  |
| `body` | string |  |
| `attachment` | file URL | nullable |
| `created_at` | datetime |  |

#### Ticket

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `ride` | uuid | nullable |
| `category` | string |  |
| `subject` | string |  |
| `status` | string |  |
| `priority` | string |  |
| `messages` | computed |  |
| `created_at` | datetime |  |
| `updated_at` | datetime |  |

#### TicketCreate

| Field | Type | Notes |
|---|---|---|
| `ride` | uuid | nullable |
| `category` | string |  |
| `subject` | string |  |
| `message` | string | write-only; First message describing the problem. |

#### TicketReply

| Field | Type | Notes |
|---|---|---|
| `body` | string |  |

#### LostItem

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `ride` | uuid |  |
| `category` | string |  |
| `description` | string |  |
| `contact_phone` | string |  |
| `status` | string |  |
| `ticket_id` | uuid | read-only |
| `created_at` | datetime |  |

#### SOSRequest

| Field | Type | Notes |
|---|---|---|
| `lat` | decimal string | optional |
| `lng` | decimal string | optional |

#### SOSAlert

| Field | Type | Notes |
|---|---|---|
| `id` | uuid |  |
| `ride` | uuid | nullable |
| `status` | string |  |
| `lat` | decimal string | nullable |
| `lng` | decimal string | nullable |
| `contacts_notified` | object |  |
| `created_at` | datetime |  |

#### PublicTrip

What someone opening a shared trip link sees (no phone numbers, no fares).

| Field | Type | Notes |
|---|---|---|
| `status` | string |  |
| `service` | string |  |
| `pickup_address` | string |  |
| `dropoff_address` | string |  |
| `provider_first_name` | string |  |
| `vehicle` | string | e.g. 'Silver Toyota Corolla · KJA 482 FT' |
| `location` | object |  |
| `updated_at` | datetime |  |
