# InfoSenior Care API — Frontend Integration Spec

This is the handoff spec for wiring a frontend to the InfoSenior Care backend (`app/main.py`, FastAPI). It is written to be followed exactly — every field is named, every shape is a real example, and every case that can show up on screen is called out.

> `app/main.py` now runs **two subsystems side by side**: the structured REST API below (facility search, onboarding, assessments, inquiries, saved facilities — Supabase Auth + Postgres, Parts 1–10) **and** the original Infomary chat agent (WebSocket conversation + its own session/dashboard routes — Parts 11–14). They share one FastAPI app, one CORS config, and one set of global error handlers, but are otherwise independent — the chat agent doesn't use Supabase Auth or the `/api/v1` versioning at all.

**Base URL**: point your client at wherever `uvicorn app.main:app` (or the deployed instance) is running — e.g. `http://localhost:8000` in dev. Every path below is relative to it. For the WebSocket (Part 11), swap the scheme: `http://` → `ws://`, `https://` → `wss://`.

- The versioned REST API (Parts 1–10) lives under the `/api/v1` prefix (e.g. `/api/v1/facilities/search`).
- Health checks live at the root: `/health`, `/health/ready`.
- The chat agent and its supporting routes (Parts 11–14) live at the **root**, unversioned — `/ws/{session_id}`, `/history/{session_id}`, `/sessions`, `/generate-title`, `/delete-session`, `/dashboard/*`, `/test-supabase`. Not under `/api/v1`.
- Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are available in dev/staging but are **disabled in production** (`ENVIRONMENT=production`) — don't rely on them being reachable outside dev.

---

## Part 1 — Authentication

There is no session/WebSocket concept anymore. Every request that needs identity sends a normal `Authorization: Bearer <token>` header. Three kinds of caller exist:

| Caller type | Token | How obtained |
|---|---|---|
| **Signed-in user** | Real Supabase JWT | Client signs in via the Supabase SDK directly (email, Google OAuth, Apple OAuth) — this backend never sees passwords or OAuth tokens. |
| **Guest** | Backend-issued guest token, always prefixed `guest_` | `POST /api/v1/auth/guest` |
| **Anonymous** | none | Only allowed on endpoints explicitly marked public below |

### 1.1 Signed-in flow
1. Client authenticates with Supabase directly (email/password, Google, or Apple) and gets a Supabase JWT.
2. Client calls `POST /api/v1/auth/sync-profile` with that JWT to create/refresh our own `profiles` row.
3. Client sends `Authorization: Bearer <supabase_jwt>` on every subsequent authenticated request.

Supabase JWTs are verified server-side (HS256 legacy secret or ES256/RS256 via JWKS, auto-detected) — no client-side change needed either way.

### 1.2 Guest flow
1. `POST /api/v1/auth/guest` → `{ "access_token": "guest_...", "token_type": "bearer" }`.
2. Store it like any bearer token and send `Authorization: Bearer guest_...`.
3. Guest tokens are self-contained signed tokens (not a DB row), valid for **30 days**, and only accepted on endpoints that explicitly allow guests (see the per-endpoint auth column below). No Supabase user is ever created for a guest.
4. **Guest limitations**: no saved-facilities list, no inquiry history — guests can browse, do onboarding, take the assessment, and submit inquiries, but `saved` and `inquiries` history endpoints require a real signed-in account. If a guest later signs up for real, merging their guest activity into the new account is not yet implemented (`migrate_guest_activity` is an explicit TODO stub server-side) — don't build a frontend flow that assumes it works yet.

### 1.3 Dev/testing-only auth endpoints
`POST /api/v1/auth/signup` and `POST /api/v1/auth/signin` are thin pass-throughs to Supabase's own email/password REST API, provided **only** so this backend can be exercised end-to-end from Swagger/Postman without a separate call to Supabase. No password ever touches this backend's own database. A real mobile/web client should keep using the Supabase SDK directly for sign-up/sign-in, not these two routes.

#### `POST /api/v1/auth/signup`
Request: `{ "email": "a@b.com", "password": "min6chars" }`
Response (`SupabaseAuthResponse`, thin pass-through of Supabase's own shape):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "user": { "id": "...", "email": "a@b.com", "...": "..." },
  "raw": { "...": "raw Supabase payload" }
}
```
If Supabase has "Confirm email" enabled (the default), `access_token` will be `null` until the confirmation link is clicked. Any Supabase-side error is relayed as an HTTP error with Supabase's own error body as `detail`.

#### `POST /api/v1/auth/signin`
Request: `{ "email": "a@b.com", "password": "..." }` → same `SupabaseAuthResponse` shape as signup, with a real `access_token` on success.

#### `POST /api/v1/auth/sync-profile`
Auth: **required user** (real Supabase JWT only — not a guest token).
Call right after any Supabase sign-in. Creates the `profiles` row on first login; on later logins refreshes `email`/`full_name`/`avatar_url` in case they changed upstream (e.g. new Google avatar). `auth_provider` is set once at creation and never overwritten on later logins even if the user later signs in with a different provider.

Response (`SyncProfileResponse`):
```json
{
  "profile": {
    "id": "3f9a1c2e-...",
    "email": "a@b.com",
    "full_name": "Jane Doe",
    "avatar_url": "https://...",
    "auth_provider": "google",
    "is_guest": false,
    "onboarding_data": null,
    "onboarding_completed": false
  },
  "created": true
}
```

#### `POST /api/v1/auth/guest`
Auth: none. Response: `{ "access_token": "guest_...", "token_type": "bearer" }`.

---

## Part 2 — Onboarding & Profile

### `POST /api/v1/onboarding/complete`
Auth: **user or guest**.
Request (`OnboardingPayload` — both fields optional, free-form JSON blobs, shape still evolving product-side):
```json
{
  "loved_one": { "relationship": "mother", "age": 78 },
  "location": { "state": "TX", "city": "Austin", "zip_code": "78701" }
}
```
Note: there is deliberately no "who you are" field — the account's own `full_name` (from signup) already covers identity.

If the caller (including a guest) has no `profiles` row yet, one is created here. Response is the updated `ProfileOut` (see shape above) with `onboarding_data` set and `onboarding_completed: true`.

### `GET /api/v1/onboarding/me`
Auth: **user or guest**. Returns the caller's `ProfileOut`. `404` if no profile row exists yet (i.e. `/onboarding/complete` was never called for this identity).

### `GET /api/v1/profile/me`
Auth: **user** (guests are not supported on this route — only real accounts). `404 "Profile not found -- call /auth/sync-profile first"` if missing.

### `PATCH /api/v1/profile/me`
Auth: **user**. Body (`ProfileUpdate`, both optional — only sent fields are updated): `{ "full_name": "Jane D.", "avatar_url": "https://..." }` → updated `ProfileOut`.

### `PATCH /api/v1/profile/loved-one`
Auth: **user**. Body: any JSON object — stored verbatim under `onboarding_data.loved_one`, replacing whatever was there before (not deep-merged). Returns updated `ProfileOut`.

---

## Part 3 — Facilities

The highest-traffic surface (Home + Search screens hit these on every load). List-style endpoints (`search`, `suggest`, `recommended`) only ever return the slim `FacilityCard` shape below — the wide detail tables are only joined in on the single-facility detail endpoint. Search/suggest/recommended responses are cached server-side (Valkey) for a few minutes, keyed by the exact query params.

### 3.1 `FacilityCard` shape (used by search/suggest/recommended/saved)
```ts
interface FacilityCard {
  id: string
  name: string
  facility_type?: string            // raw source text, e.g. "NURSING HOME"
  facility_type_category?: string   // standardized, e.g. "Nursing Home / Skilled Nursing Facility"
  city?: string
  state?: string
  zip_code?: string
  latitude?: number
  longitude?: number
  overall_rating?: number
  bed_count?: number
  ownership_type?: string
}
```

### `GET /api/v1/facilities/search`
Auth: none (public). Query params:

| Param | Type | Notes |
|---|---|---|
| `state` | string, 2–30 chars | |
| `zip_code` | string | exact match |
| `city` | string | typo-tolerant (trigram fuzzy match) |
| `name` | string | typo-tolerant |
| `facility_type` | string | raw field, typo-tolerant |
| `facility_type_category` | string | standardized field, prefer this for a fixed frontend dropdown; also typo-tolerant |
| `page` | int, default 1, min 1 | |
| `page_size` | int, default 20, 1–100 | |

`budget_min`/`budget_max`/`lat`/`lng`/`radius_miles` are **not** accepted by this endpoint despite appearing in an internal schema — there's no pricing field in the current dataset and no geo-radius query wired up yet. Don't send them; they'll be silently ignored (extra query params aren't rejected, just unused).

Response (`PaginatedFacilities`):
```json
{
  "items": [ { "id": "...", "name": "Golden Years Nursing Home", "...": "FacilityCard fields" } ],
  "page": 1,
  "page_size": 20,
  "total": 143,
  "has_more": true
}
```

### `GET /api/v1/facilities/suggest`
Auth: none. Autocomplete-style. Query: `q` (required, 1–200 chars), `limit` (default 8, 1–20). Tries an exact/substring name match first; only falls back to fuzzy trigram matching if that returns nothing at all.

Response: array of `{ id, name, city, state }`.

### `GET /api/v1/facilities/recommended`
Auth: **optional** — accepts a user, a guest, or no token at all (never 401s). Query: `limit` (default 10, 1–50). No `state` param — personalization is derived from the caller's own data instead:
- If signed in/guest **and** onboarding is complete: scoped to their onboarding location's state.
- If they also have a completed assessment: additionally scoped to that assessment's recommended facility type category.
- If nothing personalizable is available (no token, or onboarding/assessment skipped): falls back to the global highest-rated active facilities — this is the normal/common case, not an error.
- If personalized filters match zero facilities, falls back to the generic top-rated list rather than returning an empty screen.

Response: array of `FacilityCard`.

### `GET /api/v1/facilities/{facility_id}`
Auth: none. `facility_id` is a UUID (`400` if malformed, `404` if not found/inactive).

Response (`FacilityDetail` — `FacilityCard` fields plus):
```json
{
  "id": "...", "name": "...", "...": "all FacilityCard fields, plus:",
  "legal_business_name": "...",
  "address": "123 Main St",
  "county": "Travis",
  "phone": "(512) 555-0182",
  "email": "info@example.com",
  "operating_status": "Active",
  "data_source": "CMS",
  "certification_date": "2019-03-01",
  "secure_memory_care_beds": 12,
  "specialty_notes": "...",
  "nursing_home_detail": { "nh_health_inspection_star_rating": 4.0, "...": "see NursingHomeDetailOut" },
  "home_health_detail": null,
  "services": { "offers_alzheimer_dementia_care": "Yes", "...": "see FacilityServicesOut" }
}
```
`nursing_home_detail` / `home_health_detail` are mutually exclusive-ish (whichever table applies to that facility type) — expect one populated and the other `null`. `services` may also be `null` if no services row exists for that facility.

---

## Part 4 — Assessment (5-question quiz)

### `POST /api/v1/assessment/submit`
Auth: **user or guest**. Request (`AssessmentSubmit`): `{ "answers": { "primary_need": "memory_care", "...": "..." } }` — free-form dict of question→answer.

Server-side scoring is a placeholder mapping from `answers.primary_need` to a `facility_type_category`:

| `primary_need` | → recommended care type |
|---|---|
| `memory_care` | Nursing Home / Skilled Nursing Facility |
| `independent` | Residential Care / Assisted Living |
| `medical_support` | Home Health Agency |
| `end_of_life` | Hospice |
| *(anything else / missing)* | Assisted Living |

Response (`AssessmentResult`, `201`):
```json
{
  "assessment": {
    "id": "...", "answers": { "primary_need": "memory_care" },
    "recommended_care_type": "Nursing Home / Skilled Nursing Facility",
    "created_at": "2026-07-20T10:00:00Z"
  },
  "matched_facility_count": 87
}
```
`matched_facility_count` is the count of active facilities matching that category right now — useful for a "X options available" line on the results screen.

### `GET /api/v1/assessment/me/latest`
Auth: **user or guest**. Returns the caller's most recent `AssessmentOut`. `404` if they've never submitted one.

---

## Part 5 — Inquiries ("contact this facility")

Auth: **user only** on both routes below — guests cannot create or list inquiries (despite `require_user_or_guest` being used elsewhere, these two endpoints are locked to real accounts).

### `POST /api/v1/inquiries`
Request (`InquiryCreate`):
```json
{
  "facility_id": "3f9a1c2e-...",
  "message": "Looking for a room for my mother, memory care needed.",
  "contact_phone": "555-0100",
  "contact_time_preference": "Weekday afternoons"
}
```
Only `facility_id` is required. `400` if `facility_id` isn't a valid UUID, `404` if that facility doesn't exist.

Response (`InquiryOut`, `201`):
```json
{
  "id": "...", "facility_id": "3f9a1c2e-...",
  "message": "...", "contact_phone": "555-0100", "contact_time_preference": "Weekday afternoons",
  "status": "New", "created_at": "2026-07-20T10:00:00Z"
}
```

### `GET /api/v1/inquiries/me`
Returns the caller's own inquiries, newest first — array of `InquiryOut`.

---

## Part 6 — Saved Facilities

Auth: **user only** on all three routes (guests can't build a persistent saved list — matches the "no cross-device saves for guests" limitation noted in Part 1.2).

### `GET /api/v1/saved`
Returns the caller's saved facilities as an array of `FacilityCard` (newest-saved first).

### `POST /api/v1/saved/{facility_id}`
`400` if `facility_id` isn't a valid UUID, `404` if the facility doesn't exist. **Idempotent** — saving an already-saved facility is a `201` no-op, not an error (safe to fire on every tap without checking state first). Response: `{ "message": "Facility saved" }`.

### `DELETE /api/v1/saved/{facility_id}`
Also idempotent — removing something not currently saved still returns `{ "message": "Facility removed from saved" }`, not an error.

---

## Part 7 — Resources (articles/guides)

Auth: none — fully public.

### `GET /api/v1/resources`
Query: `category` (optional filter), `limit` (default 20, 1–100). Response: array of `ResourceListItem`:
```json
[ { "id": "...", "title": "How to Choose a Nursing Home", "category": "guides", "created_at": "2026-06-01T00:00:00Z" } ]
```

### `GET /api/v1/resources/{resource_id}`
`400` if malformed UUID, `404` if not found. Response (`ResourceOut`) is the same shape plus `content: string | null` (the full article body).

---

## Part 8 — Health Checks

No `/api/v1` prefix, no auth. Useful for a pre-flight "backend reachable" check and for infra probes.

### `GET /health` — liveness
Cheap check, no dependency calls. `{ "status": "ok" }`.

### `GET /health/ready` — readiness
Actually checks the DB and cache connections — use this (not `/health`) if you need to know whether the backend can currently serve real requests, e.g. right after a deploy.
```json
{ "status": "ok", "database": true, "cache": true }
```
`status` is `"degraded"` if either dependency check fails.

---

## Part 9 — Errors & Rate Limiting

Every error response is JSON with a `detail` key — there is no other shape to handle, and (unlike the old WebSocket API) failures are real HTTP status codes now, not always-200 payloads.

| Status | When | Body |
|---|---|---|
| `401` | Missing/invalid/expired bearer token on an auth-required route | `{ "detail": "Missing authentication token" }` or `{ "detail": "Invalid or expired authentication token" }` |
| `404` | Resource not found (facility, profile, assessment, resource, etc.) | `{ "detail": "<specific message>" }` |
| `422` | Request body/query fails validation | `{ "detail": "Invalid request data", "errors": [ { "loc": [...], "msg": "...", "type": "..." } ] }` (raw Pydantic error list) |
| `429` | Rate limit exceeded (see below) | `{ "detail": "Rate limit exceeded -- please slow down and try again shortly." }` |
| `500` | Any unhandled server error | `{ "detail": "An unexpected error occurred. Please try again." }` — internal details are never leaked to the client; they're logged server-side (and to Sentry, if configured) instead. |

**Rate limiting**: the backend has a Valkey-backed limiter (`RATE_LIMIT_PER_MINUTE`, default 60/min/IP) and a `429` handler wired up, but **it is not currently enforced on any route** — the middleware that would actually apply it was never added, and no endpoint has a `@limiter.limit(...)` decorator. In practice you will not see a `429` from this backend right now. Still worth handling the `429` shape defensively (build the generic "slow down" handler anyway) since this is a known gap that's likely to get closed without a client-facing announcement.

---

## Part 10 — End-to-end flow, step by step

1. **App launch**: call `GET /health` (or `/health/ready`) as an optional pre-flight check.
2. **First-time user**: either sign in via the Supabase SDK then `POST /api/v1/auth/sync-profile`, or tap "Continue as Guest" → `POST /api/v1/auth/guest` and store the returned token exactly like a real one.
3. **Onboarding**: `POST /api/v1/onboarding/complete` (works for both signed-in and guest identities).
4. **Assessment (optional)**: `POST /api/v1/assessment/submit` → use `recommended_care_type` and `matched_facility_count` to steer the results screen.
5. **Home screen**: `GET /api/v1/facilities/recommended` — send the bearer token if you have one (personalizes automatically), omit it for a fully anonymous visitor.
6. **Search screen**: `GET /api/v1/facilities/search` with explicit filters; `GET /api/v1/facilities/suggest` for the search-box autocomplete.
7. **Facility detail**: `GET /api/v1/facilities/{facility_id}`. Offer "Save" (`POST /api/v1/saved/{facility_id}`) and "Contact" (`POST /api/v1/inquiries`) actions — both require a real signed-in account, not a guest token; prompt guests to sign up if they tap either.
8. **Profile screen** (signed-in only): `GET`/`PATCH /api/v1/profile/me`.
9. **On any `401`**: treat the stored token as invalid — for a guest token this likely means it expired (30-day lifetime) or was never set; re-issue via `POST /api/v1/auth/guest` or prompt sign-in.
10. **On any `429`**: back off and retry after a short delay; don't hammer the endpoint in a tight loop. (Currently never fires — see Part 9 — but handle it anyway.)

---

# The Chat Agent (legacy Infomary subsystem)

Everything below is the original Infomary chat agent — a separate, older subsystem in the same `app/main.py` that talks over a WebSocket instead of REST, has no Supabase Auth, and lives at the **root** (no `/api/v1` prefix). It's unrelated to the facility-search REST API above except that they share one FastAPI app, one CORS allowlist, and one process.

## Part 11 — Chat Agent: Send / Receive

### 11.1 Establishing a session

A session is just a UUID the frontend generates and owns — the backend never issues one, and it has nothing to do with Supabase Auth (a chat session works the same whether or not the user is signed into the REST side of the app).

```ts
const sessionId = crypto.randomUUID()
```

- Generate this once when the user starts a new chat.
- Put it in the URL (`/chat?session=<sessionId>`) so refreshing or sharing the link resumes the same conversation.
- Reuse the same `sessionId` for every message in that conversation. A new `sessionId` = a new, empty conversation.

### 11.2 Opening the connection

```ts
const wsUrl = backendUrl.replace(/^http/, 'ws')
const socket = new WebSocket(`${wsUrl}/ws/${sessionId}`)
```

Open exactly one socket per active chat screen. Rules for its lifecycle:

- Open it when the chat screen mounts / when `sessionId` is set.
- Close it (`onclose = null` first, then `.close()`) when the screen unmounts or `sessionId` changes, to avoid leaking a stale connection.
- On `onclose`, treat it as a disconnect and reconnect automatically with backoff (e.g. 2s → 4s → 8s, cap at 10s, give up after ~5 attempts and show a "Disconnected — Retry" banner with a manual retry button).
- On `onerror`, mark the UI as disconnected (the `onclose` that typically follows will drive the reconnect).
- While disconnected, sending is not possible — the backend does **not** queue anything for you. If the user sends while disconnected, hold the message client-side (e.g. `localStorage`) and either auto-resend on reconnect or show a "Restore" affordance.
- The WebSocket handshake is **not** subject to the CORS allowlist the way normal HTTP requests are (Starlette's `CORSMiddleware` doesn't apply to the WS scope) — but the REST routes in Part 13/14 below (`/history`, `/sessions`, etc.) are, so make sure your dev origin is in `CORS_ORIGINS` for those to work.

### 11.3 Sending a message

Send exactly this shape on every user turn:

```json
{
  "message": "What memory care options are there in Austin?",
  "history": [
    { "role": "user", "content": "Hi, I'm looking for care for my mother." },
    { "role": "assistant", "content": "I'd be happy to help! What kind of care are you looking for?" }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `message` | string | yes | The new user turn, verbatim, no trimming/formatting needed. |
| `history` | array of `{role, content}` | yes (can be `[]` for the first message) | Every prior turn in this conversation so far, in order. `role` is exactly `"user"` or `"assistant"` (lowercase strings). `content` is plain text. |

Do **not** include `facility_cards` in the `history` entries you send — only `role`/`content` are read by the backend; anything else is ignored. Whatever history you have client-side is fine to send as-is — the backend itself caps it to the most recent 20 entries before using it, so you don't need to trim.

### 11.4 Receiving a reply

Every send produces exactly one reply event on the same socket — no streaming, no partial tokens, no multi-part answers. Shape:

```json
{
  "response": "Here are a few CMS-certified memory care options near Austin, TX...",
  "facility_cards": null
}
```

or, when the assistant's search produced results:

```json
{
  "response": "I didn't find a CMS-certified match for that in our database, so here's what general search turned up instead. Here are a few options near Austin...",
  "facility_cards": [
    { "source": "not_certified", "title": "Sunrise Senior Living Austin", "snippet": "Assisted living community offering...", "url": "https://example.com/sunrise-austin" }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `response` | string | **Markdown**, not plain text. Render through a Markdown component with HTML sanitization enabled — replies can contain headings, bold/italic, bullet/numbered lists, tables, links, and fenced code blocks. Render it as-is; do not append your own disclaimers or post-process it (see the disclosure note in Part 12.4). |
| `facility_cards` | array or `null` | See Part 12. On most turns this is `null` — that's the normal case, not a failure. Only render the card row when it's a non-empty array. |

**What to do with it, step by step:**
1. Parse the JSON from `event.data`.
2. Append a new assistant chat bubble containing `response` (Markdown-rendered).
3. If `facility_cards` is a non-empty array, render the card carousel directly under that bubble (see Part 12) — attach the cards to that specific message, not globally, since a conversation can have multiple card rows across different turns.
4. Clear your "sending / typing indicator" state.

### 11.5 Error replies (still arrive as normal reply events)

If something goes wrong server-side, you still get a `{"response": "...", "facility_cards": null}` event — never a different shape, never a WebSocket close, never an HTTP error code. This is deliberately different from the REST API's error handling in Part 9 — the chat agent never surfaces `4xx`/`5xx`, it always replies in-band on the socket:

- Upstream LLM rate-limited: *"I'm getting a lot of requests right now — please wait a few seconds and try again."*
- Any other per-turn failure: *"Sorry, I had trouble with that — could you try rephrasing?"*

No special-casing is needed for these beyond displaying them as a normal assistant bubble. The socket itself stays open — one bad turn never kills the connection.

---

## Part 12 — Chat Flashcards: The List

`facility_cards`, when present, is an array of 1+ cards describing facilities the assistant found for that specific turn. There are exactly two variants, distinguished by `source`. **Check `source` first**, then read the matching fields — the other variant's fields will be absent/undefined.

> Note: these are shaped nothing like the `FacilityCard` schema in Part 3 (the REST facility-search API) — same name, different subsystem, different fields. Don't share a TypeScript type between them.

### 12.1 TypeScript shape

```ts
interface ChatFacilityCard {
  source: 'cms_certified' | 'not_certified'

  // present only when source === 'cms_certified'
  name?: string                  // facility name
  facility_type_label?: string   // e.g. "Nursing Home", "Home Health Agency", "Hospice"
  city?: string
  state?: string
  phone?: string
  highlight?: string             // short badge text, e.g. a quality-rating callout — may be absent

  // present only when source === 'not_certified' (general web search fallback)
  title?: string
  snippet?: string
  url?: string
}
```

### 12.2 Example payloads

CMS-certified result:
```json
{
  "source": "cms_certified",
  "name": "Golden Years Nursing Home",
  "facility_type_label": "Nursing Home",
  "city": "Austin",
  "state": "TX",
  "phone": "(512) 555-0182",
  "highlight": "5-star CMS rating"
}
```

Web-fallback result (no certified match found):
```json
{
  "source": "not_certified",
  "title": "Sunrise Senior Living Austin",
  "snippet": "Assisted living community offering personalized care plans...",
  "url": "https://example.com/sunrise-austin"
}
```

### 12.3 Rendering rules

1. **One card row per assistant message that has cards**, positioned directly below that message's chat bubble — not a global/floating list.
2. **`cms_certified` cards** get a visually distinct "certified" treatment (e.g. green accent, a certified badge/stamp icon) — show `name`, `facility_type_label`, `city`/`state` joined with a comma, `phone` if present, and `highlight` as a small pill/badge if present.
3. **`not_certified` cards** get a visually distinct "not certified" treatment (e.g. orange accent) with an explicit label like "Not CMS-certified — from general web search" — show `title` (as a link to `url` if present, else plain text) and `snippet` if present.
4. **Multiple cards**: render as a swipeable/paginated row (prev/next controls + "`n / total`" indicator), not a long vertical list — a turn can return several cards.

### 12.4 The disclosure sentence — don't duplicate it

Whenever a turn's cards include any `not_certified` entries, the backend has already guaranteed that `response` starts with this exact sentence:

> "I didn't find a CMS-certified match for that in our database, so here's what general search turned up instead."

This is enforced server-side even if the LLM's own phrasing drops it. **Do not add your own "these aren't verified" disclaimer on top of it** — it's already baked into `response`. Just render `response` normally.

---

## Part 13 — Chat Session Management (REST)

These aren't part of the live chat turn itself — they handle session history/list management around it. They're plain root-level REST routes (not under `/api/v1`, not Supabase-authenticated), and their error shape is the standard `{"detail": "..."}` from Part 9.

### `GET /history/{session_id}`
Loads everything persisted for a session. Use when a user reopens/switches to an existing session (hydrate the chat window before opening the WebSocket for new turns).

```json
{
  "messages": [
    { "role": "user", "content": "Hi, I'm looking for care for my mother.", "facility_cards": null },
    { "role": "assistant", "content": "I'd be happy to help! What kind of care...", "facility_cards": null },
    { "role": "assistant", "content": "Here are a few options...", "facility_cards": [ { "source": "cms_certified", "...": "..." } ] }
  ]
}
```
Same `ChatFacilityCard` shape as the WebSocket event, same rule: only render a card row when `facility_cards` is a non-empty array on that message. `role`/`content` map directly onto your chat bubble model. `500 {"detail": "Failed to fetch history"}` on failure.

### `GET /sessions`
Powers a sidebar/history list of past conversations.

```json
{
  "sessions": [
    { "session_id": "3f9a...", "title": "Memory Care in Austin", "description": "Looking for memory care options", "created_at": "2026-07-18T10:32:00.000Z" },
    { "session_id": "9c1e...", "title": "New Conversation", "description": "", "created_at": "2026-07-19T08:04:11.000Z" }
  ]
}
```
Ordered newest-first. Freshly created sessions show placeholder `"New Conversation"` / `""` until titled (next endpoint). `500 {"detail": "Failed to fetch sessions"}` on failure.

### `POST /generate-title`
Call once per session, right after the **first** assistant reply lands, to auto-label it for the sidebar.

Request:
```json
{ "session_id": "3f9a...", "user_message": "the first user turn text", "ai_response": "the first assistant reply text" }
```
Response:
```json
{ "title": "Memory Care in Austin", "description": "Looking for memory care facility options" }
```
Never errors out to the client — on any internal failure it falls back to `{"title": "New Conversation", "description": ""}` with a `200`, so don't add error handling for this one beyond the normal fetch failure path. After this call, re-fetch `GET /sessions` so the sidebar picks up the new title. Track "have I titled this session yet" client-side (e.g. a ref/flag reset on new session) so you don't call this on every turn.

### `POST /delete-session`
```json
{ "session_id": "3f9a..." }
```
→ `{ "status": "deleted" }`. Removes the session and its messages. If the deleted session is the one currently open, start a new chat; otherwise just refresh the session list. `500 {"detail": "Failed to delete session"}` on failure.

### `GET /test-supabase`
Manual diagnostic route (writes a hardcoded test lead row to Supabase) — **not part of the frontend contract**, don't call it from the app. Left in for backend debugging only.

---

## Part 14 — Dashboard (internal/admin, REST)

Lead-management routes for an internal ops/admin surface, not the consumer-facing chat app. Root-level, unversioned, unauthenticated at the route level (no bearer token check) — if you're building an admin panel against these, put your own access control in front of it; the backend does not gate these by role.

### `GET /dashboard/stats`
Aggregate counts for a dashboard overview. `503 {"detail": "Dashboard unavailable — DB may be down"}` if the query fails.

### `GET /dashboard/leads`
Query: `limit` (default 100), `offset` (default 0), `status` (optional filter). Response: `{ "leads": [ { "...": "lead fields, timestamps as ISO strings" } ] }`. `503 {"detail": "Failed to fetch leads"}` on failure.

### `POST /dashboard/leads/status`
Request: `{ "lead_id": "...", "status": "Contacted" }`. `status` must be one of: `New`, `Contacted`, `Qualified`, `Converted`, `Not Interested` — `400` with the valid list in `detail` otherwise. Response: `{ "status": "updated" }`. `500 {"detail": "Failed to update status"}` on failure.

---

## Part 15 — Chat end-to-end flow, step by step

1. **New chat**: generate `sessionId` client-side → show a canned greeting bubble locally (no backend call for this) → open the WebSocket.
2. **Reopening an existing chat**: `GET /history/{sessionId}` → map each entry into your message list (carry `facility_cards` through per-message) → open the WebSocket for any new turns.
3. **User sends a message**: append the user bubble optimistically → send `{message, history}` over the socket → on the reply event, append the assistant bubble + (if non-empty) its card row → clear the loading/typing indicator.
4. **After the first reply of a brand-new session**: call `POST /generate-title`, then `GET /sessions` to refresh the sidebar.
5. **Sidebar**: `GET /sessions` on mount; on delete, `POST /delete-session` then refresh (or navigate to a new chat if the open session was the one deleted).
6. **Disconnects**: reconnect with backoff; persist the last unsent message so it isn't silently lost if the user navigates away mid-reconnect.
