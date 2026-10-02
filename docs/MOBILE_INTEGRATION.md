# Chicano Cruise — Mobile integration guide (React Native / Expo)

This is the guide for the Expo developer building the four apps against the Django API:

| App | Who uses it | Logs in with `app` = | Main role in `/auth/me/` |
|---|---|---|---|
| **User app · Cars** | customers booking cars | `user_cars` | `customer` |
| **User app · Bikes** | customers booking bikes | `user_bikes` | `customer` |
| **Driver app · Cars** | car drivers | `driver_cars` | `driver` |
| **Rider app · Bikes** | bike riders | `rider_bikes` | `rider` |

```
React Native (Expo)  ──HTTP/JSON──▶  Django REST Framework  ──▶  PostgreSQL (one database)
                                         │
                                         └─ drf-spectacular ─▶ OpenAPI 3 ─▶ Swagger UI + ReDoc
```

Django serves **no screens** for the apps. Everything the apps need is a JSON endpoint under `/api/v1/`.

---

## 1. One database, one account per phone number

All four apps and the Super Admin share **one `users` table**. A person is one row keyed by phone number; what they can do comes from the profiles attached to that row:

```
accounts.User  (phone, name, photo, status)
   ├── customers.CustomerProfile   → can book in both User apps
   ├── providers.ProviderProfile   → service = "car" (driver) or "bike" (rider)
   └── is_staff + staff_role       → Super Admin web dashboard only (/dashboard/, Django templates)
```

So a rider who also books cars logs into the Rider app **and** the User app · Cars with the same number, and `GET /auth/me/` returns `"roles": ["customer", "rider"]`. A person is either a driver *or* a rider, not both.

---

## 2. Running the API and finding it from the phone

```bash
cp backend/.env.example backend/.env
docker compose up --build                            # API on :8000, Postgres on :5432, dispatch worker
docker compose exec api python manage.py seed_demo   # demo accounts, prices, promos
```

| Where the app runs | Base URL |
|---|---|
| iOS simulator | `http://localhost:8000/api/v1` |
| Android emulator | `http://10.0.2.2:8000/api/v1` |
| Physical phone (Expo Go) on the same Wi-Fi | `http://<your-laptop-LAN-IP>:8000/api/v1` — add that IP to `DJANGO_ALLOWED_HOSTS` in `backend/.env` |

Put it in the Expo config so each build can point elsewhere:

```ts
// app.config.ts
export default { expo: { name: "Chicano Cruise", extra: { apiUrl: process.env.EXPO_PUBLIC_API_URL ?? "http://10.0.2.2:8000/api/v1" } } };
```

Native apps don't need CORS. Expo **Web** does: add its origin to `CORS_ALLOWED_ORIGINS`.

### Documentation you can click

| URL | What it is |
|---|---|
| `http://localhost:8000/api/docs/` | **Swagger UI** — every endpoint, try it live. Click **Authorize**, paste `Bearer <access>` (or just the token). |
| `http://localhost:8000/api/redoc/` | **ReDoc** — clean read-only reference to share. |
| `http://localhost:8000/api/schema/` | Raw **OpenAPI 3** (YAML; `?format=json` for JSON). |
| `docs/API_REFERENCE.md` | The same endpoints as tables (URL, method, auth, body, response, errors). |

### Generate TypeScript types from the schema (recommended)

```bash
npx openapi-typescript http://localhost:8000/api/schema/ -o src/api/schema.d.ts
```

```ts
import type { components } from "./schema";
type Ride = components["schemas"]["Ride"];
type FareQuote = components["schemas"]["FareQuote"];
```

Re-run it whenever the backend changes; TypeScript then shows you exactly which screens a change breaks. Request and response bodies are separate schemas (e.g. `RideRequestRequest` vs `Ride`) because `COMPONENT_SPLIT_REQUEST` is on.

---

## 3. Conventions (read once, applies everywhere)

| Topic | Rule |
|---|---|
| Auth header | `Authorization: Bearer <access_token>` |
| Content type | `application/json`; `multipart/form-data` only for file uploads (documents, profile photo) |
| **Money** | Always **integers in kobo**. ₦1 = 100 kobo. `total_amount: 350000` → show **₦3,500**. Never do float maths on naira. |
| IDs | UUID strings |
| Times | ISO 8601 with offset, e.g. `2026-09-25T14:03:11.402+01:00`. Send the same format. |
| Coordinates | Decimal strings or numbers with up to 6 decimals, e.g. `"6.447400"` |
| Lists | Paginated lists return `{count, next, previous, results}` (20 per page, `?page=2`). Short lists (saved places, cards, ride types) are plain arrays — Swagger shows which. |
| Empty | `204 No Content` means "nothing right now" (e.g. no incoming offer). No body. |
| Versioning | Everything is under `/api/v1/`. Breaking changes will ship as `/api/v2/` so old app builds keep working. |

### The error format (every error, every endpoint)

```json
{
  "error": {
    "code": "quote_expired",
    "message": "This fare has expired. Get a new estimate.",
    "details": {}
  }
}
```

- **Switch on `code`**, show `message` to the user (it's written for end users).
- For form errors (`validation_error`, `invalid_phone`), `details` maps field → messages: `{"phone": ["Enter a valid Nigerian phone number."]}`.

| HTTP | Common `code`s | What the app should do |
|---|---|---|
| 400 | `validation_error`, `invalid_phone`, `otp_invalid`, `otp_expired`, `otp_locked`, `outside_service_zone`, `promo_*` | Show the message / field errors |
| 401 | `not_authenticated`, `token_not_valid` | Refresh the token once and retry; if refresh fails → login screen |
| 402 | `insufficient_wallet`, `payment_failed` | Show "Change payment method" |
| 403 | `permission_denied`, `account_suspended`, `provider_not_approved` | `account_suspended` → "Account on hold" screen; `provider_not_approved` → onboarding screen |
| 404 | `not_found`, `no_active_ride` | Record doesn't exist or isn't yours |
| 409 | `invalid_state`, `quote_expired`, `quote_used`, `active_ride_exists`, `offer_expired`, `package_not_collected`, `already_rated`, … | Refresh the screen's data; the record moved on |
| 429 | `throttled` (`details.retry_after_seconds`) | Wait, then retry |

---

## 4. Login (all four apps)

Phone number + SMS code. Sign-up and login are the same two calls.

```
POST /auth/otp/request/   {"phone": "0803 412 5567"}
  → 200 {"phone": "+2348034125567", "expires_in_seconds": 300, "resend_after_seconds": 30, "debug_code": "482917"}

POST /auth/otp/verify/    {"phone": "+2348034125567", "code": "482917", "app": "user_cars"}
  → 200 {"access": "...", "refresh": "...", "is_new_user": true, "user": {..., "roles": ["customer"]}}
```

- `debug_code` is only returned in development (`OTP_DEBUG_RETURN_CODE=true`) so you can log in without SMS.
- `app` must be the app's own value (`user_cars`, `user_bikes`, `driver_cars`, `rider_bikes`). The User apps create the customer profile automatically.
- `is_new_user: true` → show "What's your name?" then `PATCH /auth/me/ {"first_name", "last_name"}`.
- **Driver/Rider apps:** if `roles` has no `driver`/`rider`, call `POST /provider/apply/ {"service": "car"|"bike", "city": "Lagos"}` and show onboarding.

### Tokens

| Token | Lifetime | Store in |
|---|---|---|
| `access` | 30 minutes | memory (and SecureStore for app restarts) |
| `refresh` | 30 days, **rotates** | `expo-secure-store` — never AsyncStorage |

`POST /auth/token/refresh/ {"refresh"}` returns a **new pair**; the old refresh token stops working, so always save the new one. Logout: `POST /auth/logout/ {"refresh"}` then `DELETE /auth/devices/{id}/`.

### Drop-in API client (axios + SecureStore + auto refresh)

```ts
// src/api/client.ts
import axios, { AxiosError } from "axios";
import * as SecureStore from "expo-secure-store";
import Constants from "expo-constants";

export const API_URL: string = Constants.expoConfig?.extra?.apiUrl;
const REFRESH_KEY = "cc_refresh";
let accessToken: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, any> = {}) { super(message); }
}

export async function saveSession(access: string, refresh: string) {
  accessToken = access;
  await SecureStore.setItemAsync(REFRESH_KEY, refresh);
}
export async function clearSession() {
  accessToken = null;
  await SecureStore.deleteItemAsync(REFRESH_KEY);
}

export const api = axios.create({ baseURL: API_URL, timeout: 15000 });

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

// One shared refresh for all requests that hit 401 at the same time.
let refreshing: Promise<string> | null = null;
async function refreshAccess(): Promise<string> {
  refreshing ??= (async () => {
    const refresh = await SecureStore.getItemAsync(REFRESH_KEY);
    if (!refresh) throw new Error("no session");
    const { data } = await axios.post(`${API_URL}/auth/token/refresh/`, { refresh });
    await saveSession(data.access, data.refresh);        // refresh tokens rotate: keep the new one
    return data.access as string;
  })().finally(() => { refreshing = null; });
  return refreshing;
}

export let onLoggedOut: () => void = () => {};             // set this from your auth provider
export const setOnLoggedOut = (fn: () => void) => { onLoggedOut = fn; };

api.interceptors.response.use(undefined, async (error: AxiosError<any>) => {
  const original: any = error.config;
  if (error.response?.status === 401 && original && !original._retried && !original.url?.includes("/auth/")) {
    original._retried = true;
    try {
      original.headers.Authorization = `Bearer ${await refreshAccess()}`;
      return api.request(original);
    } catch {
      await clearSession();
      onLoggedOut();
    }
  }
  const body = error.response?.data?.error;
  if (body) throw new ApiError(error.response!.status, body.code, body.message, body.details);
  throw new ApiError(0, "network_error", "No internet connection. Check your data and try again.");
});

// On app start: restore the session (returns false → show login)
export async function restoreSession(): Promise<boolean> {
  try { await refreshAccess(); return true; } catch { return false; }
}
```

Usage:

```ts
const { data: quotes } = await api.post("/rides/estimate/", { service: "car", pickup, dropoff });
try {
  await api.post("/rides/", { quote_id, payment_method: "cash", pickup_address, dropoff_address });
} catch (e) {
  if (e instanceof ApiError && e.code === "quote_expired") refetchEstimate();
  else Alert.alert("Couldn't book", (e as ApiError).message);
}
```

### Suggested libraries

| Need | Package |
|---|---|
| HTTP | `axios` (above) |
| Server state + polling | `@tanstack/react-query` (`refetchInterval` does the polling below) |
| Tokens | `expo-secure-store` |
| GPS (driver/rider in background) | `expo-location` + `expo-task-manager` |
| Push | `expo-notifications` (Expo push tokens; backend `PUSH_BACKEND=expo`) |
| Maps | `react-native-maps` |
| Document photos | `expo-image-picker` / `expo-document-picker` |
| Types | `openapi-typescript` (section 2) |

---

## 5. Live updates: polling, not sockets

The API is plain HTTP, so screens that change on their own poll. The server also sends a push for every important change, so the app can refetch immediately when one arrives.

| App · screen | Call | Every |
|---|---|---|
| User apps · finding a driver / trip in progress | `GET /rides/{id}/` | 3 s while `status` is `searching`/`accepted`/`arrived`, 5 s while `in_progress`; stop when `is_active` is false |
| User apps · app launch | `GET /rides/active/` (404 `no_active_ride` = nothing to resume) | once |
| Driver/Rider · online, no trip | `GET /provider/offers/current/` (200 = show request, 204 = nothing) | 3 s |
| Driver/Rider · on a trip | `GET /provider/trips/current/` (catches customer cancellations) | 5 s |
| Driver/Rider · online | `POST /provider/location/ {lat, lng, heading}` | every 4–5 s (limit 120/min) |
| Driver/Rider · app launch | `GET /provider/trips/current/` (204 = no trip) | once |

With React Query:

```ts
const ride = useQuery({
  queryKey: ["ride", id],
  queryFn: () => api.get(`/rides/${id}/`).then((r) => r.data),
  refetchInterval: (q) => (q.state.data?.is_active ? (q.state.data.status === "in_progress" ? 5000 : 3000) : false),
});
```

### Ride statuses → what to show (User apps)

| `status` | Screen |
|---|---|
| `scheduled` | Upcoming trip card (cars only) |
| `searching` | "Finding your driver/rider…" + Cancel |
| `accepted` | Driver/rider card, vehicle, `provider_location` on the map, `eta_to_pickup_seconds` |
| `arrived` | "Your driver is outside" (free waiting timer runs; `cancellation_fee_if_cancelled_now` shows any fee) |
| `in_progress` | Trip map, Share trip (`share_url`), SOS |
| `completed` | Receipt → rate + tip (`POST /rides/{id}/rate/`) |
| `cancelled` | "Trip cancelled" (`cancelled_by`, `cancel_reason`, `cancellation_fee_amount`) |
| `no_provider` | "No drivers nearby, try again" |

Bike rides are package deliveries (`is_delivery: true`). Book them with `package: {kind, size, contents?, fragile?, recipient_name, recipient_phone}` (400 `package_required` without it); every ride, offer and trip then returns the same `package` object (null for cars).

---

## 6. Endpoints per app

Full request/response bodies: Swagger, or `docs/API_REFERENCE.md`. All paths below are relative to `/api/v1`.

### Shared by all four apps

| Screen | Call |
|---|---|
| Login | `POST /auth/otp/request/`, `POST /auth/otp/verify/` |
| Session | `POST /auth/token/refresh/`, `POST /auth/logout/` |
| Profile | `GET/PATCH/DELETE /auth/me/` (PATCH as multipart to upload `photo`) |
| Push | `POST /auth/devices/ {app, platform, push_token, app_version}`, `DELETE /auth/devices/{id}/` |
| Inbox | `GET /support/notifications/`, `POST /support/notifications/read-all/` |
| Help | `GET/POST /support/tickets/`, `GET /support/tickets/{id}/`, `POST /support/tickets/{id}/reply/` |

### User app · Cars and User app · Bikes (customer)

Both User apps use the same endpoints; the difference is `service: "car"` or `"bike"`.

| Screen | Call |
|---|---|
| Home: saved places | `GET/POST /customer/places/`, `PATCH/DELETE /customer/places/{id}/` |
| Ride options | `GET /ride-types/?service=car` (or `bike`) |
| Fare estimate | `POST /rides/estimate/ {service, pickup:{lat,lng}, dropoff:{lat,lng}, stops?, promo_code?}` → one quote per ride type, valid 10 min |
| Book | `POST /rides/ {quote_id, payment_method: cash|card|wallet, payment_method_id?, pickup_address, dropoff_address, pickup_note?, scheduled_for?}` |
| Tracking | `GET /rides/{id}/` (poll), `GET /rides/active/` |
| Cancel | `POST /rides/{id}/cancel/ {reason}` — check `cancellation_fee_if_cancelled_now` first and warn |
| Rate + tip | `POST /rides/{id}/rate/ {stars, tags, comment, tip_amount?}` or later `POST /rides/{id}/tip/` |
| Card declined after trip | `POST /rides/{id}/retry-payment/ {payment_method, payment_method_id?}` |
| Safety | `POST /rides/{id}/sos/ {lat, lng}`, share link = `share_url` from the ride, emergency contacts `GET/POST /customer/emergency-contacts/` |
| Trips tab | `GET /rides/?status=completed,cancelled&service=car&page=1`; Upcoming: `?status=scheduled` |
| Wallet | `GET /wallet/`, `GET /wallet/transactions/`, `POST /wallet/top-up/` |
| Cards | `GET/POST /wallet/payment-methods/`, `DELETE /wallet/payment-methods/{id}/` |
| Promo | `POST /wallet/promos/validate/ {code, service}`, then pass `promo_code` to estimate |
| Lost item | `POST /support/lost-items/ {ride, category, description, contact_phone}` |
| Settings | `GET/PATCH /customer/profile/` (`data_saver`) |

Booking example:

```ts
const quotes = (await api.post("/rides/estimate/", {
  service: "bike",
  pickup: { lat: 6.5095, lng: 3.3711 },
  dropoff: { lat: 6.5000, lng: 3.3540 },
  promo_code: "BIKE300",
})).data;                                   // [{quote_id, ride_type:{code,name,...}, total_amount, discount_amount, breakdown, expires_at}]

const ride = (await api.post("/rides/", {
  quote_id: quotes[0].quote_id,
  payment_method: "cash",
  pickup_address: "Herbert Macaulay Way, Yaba",
  dropoff_address: "Adeniran Ogunsanya, Surulere",
  pickup_note: "In front of the pharmacy",
})).data;                                   // status "searching" → start polling GET /rides/{ride.id}/
```

### Driver app · Cars and Rider app · Bikes (provider)

Same endpoints for both; `service` on the profile is `car` (driver) or `bike` (rider). Use `kind` ("driver"/"rider") in UI copy.

**Onboarding**

| Step | Call |
|---|---|
| Register as driver/rider | `POST /provider/apply/ {service, city, first_name?, last_name?}` |
| Checklist screen | `GET /provider/onboarding/` → `steps[]` with `state` = `done` / `in_review` / `action_needed` / `todo` |
| Vehicle | `GET/POST /provider/vehicles/ {make, model, year, color, plate_number, seats, has_rider_helmet, has_delivery_box, has_reflective_vest, ...}` |
| Documents | `POST /provider/documents/` **multipart**: `doc_type`, `file`, `back_file?`, `number?`, `expires_at?`; `GET /provider/documents/` shows `status` + `rejection_reason` |
| Bank + profile | `GET/PATCH /provider/me/` (bank_name, bank_account_number, bank_account_name) |

Required documents — cars: `drivers_licence`, `insurance`, `vehicle_registration`; bikes: `riders_licence`, `insurance`, `vehicle_registration`. Staff approve in the Super Admin; the app gets a push (`provider_status`). Until approved, trip endpoints answer 403 `provider_not_approved`.

```ts
const form = new FormData();
form.append("doc_type", "drivers_licence");
form.append("number", "LAG-12345-AB");
form.append("expires_at", "2028-05-01");
form.append("file", { uri: photo.uri, name: "licence.jpg", type: "image/jpeg" } as any);
await api.post("/provider/documents/", form, { headers: { "Content-Type": "multipart/form-data" } });
```

**Going online and taking trips**

| Step | Call |
|---|---|
| Online / offline toggle | `POST /provider/status/ {is_online: true}` — 403 `provider_not_approved`, or 409 explaining why not: `no_approved_vehicle`, `helmet_required` (bike without the rider's helmet), `document_expired` (`details.doc_types`), `active_ride` (going offline mid-trip) |
| Send GPS | `POST /provider/location/ {lat, lng, heading}` every 4–5 s while online |
| Incoming request | `GET /provider/offers/current/` (poll 3 s) → full-screen card, countdown from `seconds_left` (15 s) |
| Accept / decline | `POST /provider/offers/{id}/accept/` (→ trip) · `POST /provider/offers/{id}/decline/` (204). 409 `offer_expired` if too late |
| Arrived at pickup | `POST /provider/trips/{id}/arrive/` |
| **Bikes only:** package collected | `POST /provider/trips/{id}/package-collected/` — **required before start** (409 `package_not_collected`). `helmet-check/` is the old name and still works |
| Start | `POST /provider/trips/{id}/start/` |
| Complete | `POST /provider/trips/{id}/complete/` |
| Cash trip: collect | show "Collect ₦X" from `cash_due_amount`, then `POST /provider/trips/{id}/collect-cash/` |
| Cancel before pickup | `POST /provider/trips/{id}/cancel/ {reason}` (ride goes to someone else) |
| Customer didn't show | `POST /provider/trips/{id}/no-show/` (after the free waiting time; customer pays the fee, credited to you) |
| Rate customer | `POST /provider/trips/{id}/rate/ {stars, tags, comment}` |
| SOS | `POST /provider/sos/ {lat, lng}` |
| History | `GET /provider/trips/?page=1` |

Every trip action returns the updated trip, so just replace your state with the response.

**Earnings**

| Screen | Call |
|---|---|
| Earnings (day/week/month) | `GET /provider/earnings/summary/?period=week` → totals, `by_day` chart, `balance_amount` |
| Statement | `GET /provider/earnings/ledger/` |
| Payouts | `GET /provider/payouts/`, `POST /provider/payouts/instant/` (₦100 fee, min ₦1,000) |
| Bonuses | `GET /provider/incentives/` |

`balance_amount` can be **negative** for cash-heavy drivers/riders: they hold the cash and owe the commission. Example: a ₦3,500 cash trip with 15% commission → ledger `+350000` fare, `−52500` commission, `−350000` cash kept → balance `−52500` (owes ₦525).

---

## 7. Push notifications

1. Get an Expo push token with `expo-notifications`.
2. `POST /auth/devices/ {"app": "rider_bikes", "platform": "android", "push_token": "ExponentPushToken[...]", "app_version": "1.0.0"}` after login and whenever the token changes (it upserts).
3. Every push has a `data.type` for deep-linking (the same payload is in `GET /support/notifications/`):

| `data.type` | Extra keys | Open |
|---|---|---|
| `ride_offer` | `offer_id`, `ride_id` | Incoming request card (then `GET /provider/offers/current/`) |
| `ride_update` | `ride_id`, `status` | Trip screen (refetch the ride) |
| `provider_status` | `status` | Onboarding / home |
| `document_status` | `document_id`, `status` | Documents screen |
| `vehicle_status` | `vehicle_id`, `status` | Vehicle screen |
| `payout` | `payout_id` | Payouts |
| `support_reply` | `ticket_id` | Ticket thread |
| `sos` | `sos_id` | Safety screen |
| `lost_item` | `ride_id` | Trip detail (driver/rider) |

In development the backend logs pushes to the console instead of sending them (`PUSH_BACKEND=console`).

---

## 8. Payments

- **Cash**: nothing to do in the app until the end; drivers/riders confirm with `collect-cash`.
- **Card**: tokenise the card in the app with the Paystack SDK, then `POST /wallet/payment-methods/ {gateway_token, brand, last4, exp_month, exp_year}`. Card numbers and CVV must never be sent to this API. Cards are charged when the trip completes; if that fails the ride shows `payment_status: "failed"` → "Payment didn't go through" → `retry-payment`.
- **Wallet**: `POST /rides/` returns 402 `insufficient_wallet` (with `balance_amount` and `required_amount`) if the balance is too low.
- In development the gateway is a dummy: any token works, and tokens starting with `fail_` are declined so you can build the error screens.

---

## 9. Demo accounts (after `seed_demo`)

| Who | Phone | Notes |
|---|---|---|
| Customer Ada | `08030000001` | ₦10,000 wallet credit, saved Home (Lekki) + Work (VI) |
| Customer Tunde | `08030000002` | |
| Driver Emeka | `08050000001` | Approved, online in Lekki, Toyota Corolla (Cruise) |
| Driver Chinedu | `08050000002` | Approved, online in V.I., Sienna (Cruise, XL, Premium) |
| Driver Kelechi | `08050000003` | Under review (approve him in the admin) |
| Rider Musa | `08070000001` | Approved, online in Yaba |
| Rider Sani | `08070000002` | Under review |
| Staff | `admin@chicanocruise.test` / `ChicanoDemo2026!` | Super Admin dashboard at `http://localhost:8000/dashboard/` |

The OTP code comes back as `debug_code`. Promo codes: `WELCOME10`, `BIKE300`.

Demo drivers/riders only get offers while their GPS is fresh; the `worker` container runs `dispatch_worker --keep-demo-fresh` to keep them "online". To test a full trip from your phone, log into the User app as Ada and into the Driver app as Emeka on a second device (or with Swagger), book from Lekki, and accept.
