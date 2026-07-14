
# """
# End-to-end flow tests -- simulate the actual app's user journey against a
# real (local) Postgres + Valkey, using signed JWTs shaped like Supabase's.
# Covers Google login, Apple login, and guest sessions distinctly, per the
# product requirement to track provider separately.
# """
# import pytest

# from tests.conftest import make_supabase_jwt


# @pytest.mark.asyncio
# async def test_google_signin_creates_profile_with_provider_tracked(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', full_name='Google User')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["created"] is True
#     assert body["profile"]["auth_provider"] == "google"
#     assert body["profile"]["full_name"] == "Google User"


# @pytest.mark.asyncio
# async def test_apple_signin_creates_profile_with_provider_tracked(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', full_name='Apple User')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["profile"]["auth_provider"] == "apple"
#     assert body["profile"]["full_name"] == "Apple User"


# @pytest.mark.asyncio
# async def test_google_and_apple_profiles_are_distinct_users(client):
#     google_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', email='same@example.com')}"}
#     apple_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', email='same@example.com')}"}

#     r1 = await client.post("/api/v1/auth/sync-profile", headers=google_headers)
#     r2 = await client.post("/api/v1/auth/sync-profile", headers=apple_headers)

#     assert r1.json()["profile"]["id"] != r2.json()["profile"]["id"]
#     assert r1.json()["profile"]["auth_provider"] == "google"
#     assert r2.json()["profile"]["auth_provider"] == "apple"


# @pytest.mark.asyncio
# async def test_missing_token_rejected(client):
#     resp = await client.get("/api/v1/profile/me")
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_invalid_token_rejected(client):
#     resp = await client.get("/api/v1/profile/me", headers={"Authorization": "Bearer garbage"})
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_full_user_journey(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='email', email='journey@example.com')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200

#     onboarding_payload = {
#         "who_you_are": {"relationship": "daughter"},
#         "loved_one": {"name": "Mom", "age": 82},
#         "location": {"zip": "90210", "radius_miles": 25},
#     }
#     resp = await client.post("/api/v1/onboarding/complete", json=onboarding_payload, headers=headers)
#     assert resp.status_code == 200
#     assert resp.json()["onboarding_completed"] is True

#     resp = await client.get("/api/v1/facilities/search?state=CA&page=1&page_size=5")
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["total"] > 0
#     assert len(body["items"]) <= 5
#     facility_id = body["items"][0]["id"]

#     resp = await client.get(f"/api/v1/facilities/{facility_id}")
#     assert resp.status_code == 200
#     assert resp.json()["id"] == facility_id

#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 201

#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 201

#     resp = await client.get("/api/v1/saved", headers=headers)
#     assert resp.status_code == 200
#     saved_ids = [f["id"] for f in resp.json()]
#     assert facility_id in saved_ids
#     assert saved_ids.count(facility_id) == 1

#     resp = await client.delete(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 200
#     resp = await client.get("/api/v1/saved", headers=headers)
#     assert facility_id not in [f["id"] for f in resp.json()]

#     resp = await client.post(
#         "/api/v1/inquiries",
#         json={"facility_id": facility_id, "message": "Interested in a tour", "contact_phone": "5551234567"},
#         headers=headers,
#     )
#     assert resp.status_code == 201
#     assert resp.json()["status"] == "pending"

#     resp = await client.get("/api/v1/inquiries/me", headers=headers)
#     assert resp.status_code == 200
#     assert len(resp.json()) >= 1

#     resp = await client.post(
#         "/api/v1/assessment/submit",
#         json={"answers": {"primary_need": "memory_care"}},
#         headers=headers,
#     )
#     assert resp.status_code == 201
#     assert resp.json()["assessment"]["recommended_care_type"] == "Nursing Home"

#     resp = await client.get("/api/v1/assessment/me/latest", headers=headers)
#     assert resp.status_code == 200


# @pytest.mark.asyncio
# async def test_guest_can_browse_but_inquiry_and_save_are_login_locked(client):
#     """
#     Product rule: guests can browse/search/view freely, but submitting an
#     inquiry form or saving/liking a facility requires a real login -- the
#     guest token must NOT work for these two write actions (the app shows a
#     login prompt before ever reaching the API for them). Onboarding and
#     assessment remain guest-accessible on purpose (not considered a
#     "form submit" / "like" action for this rule).
#     """
#     resp = await client.post("/api/v1/auth/guest")
#     assert resp.status_code == 200
#     guest_token = resp.json()["access_token"]
#     headers = {"Authorization": f"Bearer {guest_token}"}

#     # Browsing/search still works with no login at all.
#     search_resp = await client.get("/api/v1/facilities/search?page=1&page_size=1")
#     assert search_resp.status_code == 200
#     facility_id = search_resp.json()["items"][0]["id"]

#     # Inquiry submit is now login-locked -- guest token must be rejected.
#     resp = await client.post(
#         "/api/v1/inquiries",
#         json={"facility_id": facility_id, "message": "Guest inquiry"},
#         headers=headers,
#     )
#     assert resp.status_code == 401

#     # Save/like is now login-locked -- guest token must be rejected.
#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 401

#     resp = await client.get("/api/v1/profile/me", headers=headers)
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_nonexistent_facility_returns_404(client):
#     resp = await client.get("/api/v1/facilities/00000000-0000-0000-0000-000000000000")
#     assert resp.status_code == 404


# @pytest.mark.asyncio
# async def test_invalid_facility_id_returns_400(client):
#     resp = await client.get("/api/v1/facilities/not-a-uuid")
#     assert resp.status_code == 400


# @pytest.mark.asyncio
# async def test_resources_list_empty_ok(client):
#     resp = await client.get("/api/v1/resources")
#     assert resp.status_code == 200
#     assert isinstance(resp.json(), list)










































# """
# End-to-end flow tests -- simulate the actual app's user journey against a
# real (local) Postgres + Valkey, using signed JWTs shaped like Supabase's.
# Covers Google login, Apple login, and guest sessions distinctly, per the
# product requirement to track provider separately.
# """
# import pytest

# from tests.conftest import make_supabase_jwt


# @pytest.mark.asyncio
# async def test_google_signin_creates_profile_with_provider_tracked(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', full_name='Google User')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["created"] is True
#     assert body["profile"]["auth_provider"] == "google"
#     assert body["profile"]["full_name"] == "Google User"


# @pytest.mark.asyncio
# async def test_apple_signin_creates_profile_with_provider_tracked(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', full_name='Apple User')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["profile"]["auth_provider"] == "apple"
#     assert body["profile"]["full_name"] == "Apple User"


# @pytest.mark.asyncio
# async def test_google_and_apple_profiles_are_distinct_users(client):
#     google_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', email='same@example.com')}"}
#     apple_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', email='same@example.com')}"}

#     r1 = await client.post("/api/v1/auth/sync-profile", headers=google_headers)
#     r2 = await client.post("/api/v1/auth/sync-profile", headers=apple_headers)

#     assert r1.json()["profile"]["id"] != r2.json()["profile"]["id"]
#     assert r1.json()["profile"]["auth_provider"] == "google"
#     assert r2.json()["profile"]["auth_provider"] == "apple"


# @pytest.mark.asyncio
# async def test_missing_token_rejected(client):
#     resp = await client.get("/api/v1/profile/me")
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_invalid_token_rejected(client):
#     resp = await client.get("/api/v1/profile/me", headers={"Authorization": "Bearer garbage"})
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_full_user_journey(client):
#     headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='email', email='journey@example.com')}"}

#     resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
#     assert resp.status_code == 200

#     onboarding_payload = {
#         "who_you_are": {"relationship": "daughter"},
#         "loved_one": {"name": "Mom", "age": 82},
#         "location": {"zip": "90210", "radius_miles": 25},
#     }
#     resp = await client.post("/api/v1/onboarding/complete", json=onboarding_payload, headers=headers)
#     assert resp.status_code == 200
#     assert resp.json()["onboarding_completed"] is True

#     resp = await client.get("/api/v1/facilities/search?state=CA&page=1&page_size=5")
#     assert resp.status_code == 200
#     body = resp.json()
#     assert body["total"] > 0
#     assert len(body["items"]) <= 5
#     facility_id = body["items"][0]["id"]

#     resp = await client.get(f"/api/v1/facilities/{facility_id}")
#     assert resp.status_code == 200
#     assert resp.json()["id"] == facility_id

#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 201

#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 201

#     resp = await client.get("/api/v1/saved", headers=headers)
#     assert resp.status_code == 200
#     saved_ids = [f["id"] for f in resp.json()]
#     assert facility_id in saved_ids
#     assert saved_ids.count(facility_id) == 1

#     resp = await client.delete(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 200
#     resp = await client.get("/api/v1/saved", headers=headers)
#     assert facility_id not in [f["id"] for f in resp.json()]

#     resp = await client.post(
#         "/api/v1/inquiries",
#         json={"facility_id": facility_id, "message": "Interested in a tour", "contact_phone": "5551234567"},
#         headers=headers,
#     )
#     assert resp.status_code == 201
#     assert resp.json()["status"] == "pending"

#     resp = await client.get("/api/v1/inquiries/me", headers=headers)
#     assert resp.status_code == 200
#     assert len(resp.json()) >= 1

#     resp = await client.post(
#         "/api/v1/assessment/submit",
#         json={"answers": {"primary_need": "memory_care"}},
#         headers=headers,
#     )
#     assert resp.status_code == 201
#     assert resp.json()["assessment"]["recommended_care_type"] == "Nursing Home / Skilled Nursing Facility"

#     resp = await client.get("/api/v1/assessment/me/latest", headers=headers)
#     assert resp.status_code == 200


# @pytest.mark.asyncio
# async def test_guest_can_browse_but_inquiry_and_save_are_login_locked(client):
#     """
#     Product rule: guests can browse/search/view freely, but submitting an
#     inquiry form or saving/liking a facility requires a real login -- the
#     guest token must NOT work for these two write actions (the app shows a
#     login prompt before ever reaching the API for them). Onboarding and
#     assessment remain guest-accessible on purpose (not considered a
#     "form submit" / "like" action for this rule).
#     """
#     resp = await client.post("/api/v1/auth/guest")
#     assert resp.status_code == 200
#     guest_token = resp.json()["access_token"]
#     headers = {"Authorization": f"Bearer {guest_token}"}

#     # Browsing/search still works with no login at all.
#     search_resp = await client.get("/api/v1/facilities/search?page=1&page_size=1")
#     assert search_resp.status_code == 200
#     facility_id = search_resp.json()["items"][0]["id"]

#     # Inquiry submit is now login-locked -- guest token must be rejected.
#     resp = await client.post(
#         "/api/v1/inquiries",
#         json={"facility_id": facility_id, "message": "Guest inquiry"},
#         headers=headers,
#     )
#     assert resp.status_code == 401

#     # Save/like is now login-locked -- guest token must be rejected.
#     resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
#     assert resp.status_code == 401

#     resp = await client.get("/api/v1/profile/me", headers=headers)
#     assert resp.status_code == 401


# @pytest.mark.asyncio
# async def test_nonexistent_facility_returns_404(client):
#     resp = await client.get("/api/v1/facilities/00000000-0000-0000-0000-000000000000")
#     assert resp.status_code == 404


# @pytest.mark.asyncio
# async def test_invalid_facility_id_returns_400(client):
#     resp = await client.get("/api/v1/facilities/not-a-uuid")
#     assert resp.status_code == 400


# @pytest.mark.asyncio
# async def test_resources_list_empty_ok(client):
#     resp = await client.get("/api/v1/resources")
#     assert resp.status_code == 200
#     assert isinstance(resp.json(), list)

























"""
End-to-end flow tests -- simulate the actual app's user journey against a
real (local) Postgres + Valkey, using signed JWTs shaped like Supabase's.
Covers Google login, Apple login, and guest sessions distinctly, per the
product requirement to track provider separately.
"""
import pytest

from tests.conftest import make_supabase_jwt


@pytest.mark.asyncio
async def test_google_signin_creates_profile_with_provider_tracked(client):
    headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', full_name='Google User')}"}

    resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["profile"]["auth_provider"] == "google"
    assert body["profile"]["full_name"] == "Google User"


@pytest.mark.asyncio
async def test_apple_signin_creates_profile_with_provider_tracked(client):
    headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', full_name='Apple User')}"}

    resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["auth_provider"] == "apple"
    assert body["profile"]["full_name"] == "Apple User"


@pytest.mark.asyncio
async def test_google_and_apple_profiles_are_distinct_users(client):
    google_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='google', email='same@example.com')}"}
    apple_headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='apple', email='same@example.com')}"}

    r1 = await client.post("/api/v1/auth/sync-profile", headers=google_headers)
    r2 = await client.post("/api/v1/auth/sync-profile", headers=apple_headers)

    assert r1.json()["profile"]["id"] != r2.json()["profile"]["id"]
    assert r1.json()["profile"]["auth_provider"] == "google"
    assert r2.json()["profile"]["auth_provider"] == "apple"


@pytest.mark.asyncio
async def test_missing_token_rejected(client):
    resp = await client.get("/api/v1/profile/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_rejected(client):
    resp = await client.get("/api/v1/profile/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_full_user_journey(client):
    headers = {"Authorization": f"Bearer {make_supabase_jwt(provider='email', email='journey@example.com')}"}

    resp = await client.post("/api/v1/auth/sync-profile", headers=headers)
    assert resp.status_code == 200

    onboarding_payload = {
        "loved_one": {"name": "Mom", "age": 82},
        "location": {"zip": "90210", "radius_miles": 25},
    }
    resp = await client.post("/api/v1/onboarding/complete", json=onboarding_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["onboarding_completed"] is True

    resp = await client.get("/api/v1/facilities/search?state=CA&page=1&page_size=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert len(body["items"]) <= 5
    facility_id = body["items"][0]["id"]

    resp = await client.get(f"/api/v1/facilities/{facility_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == facility_id

    resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
    assert resp.status_code == 201

    resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
    assert resp.status_code == 201

    resp = await client.get("/api/v1/saved", headers=headers)
    assert resp.status_code == 200
    saved_ids = [f["id"] for f in resp.json()]
    assert facility_id in saved_ids
    assert saved_ids.count(facility_id) == 1

    resp = await client.delete(f"/api/v1/saved/{facility_id}", headers=headers)
    assert resp.status_code == 200
    resp = await client.get("/api/v1/saved", headers=headers)
    assert facility_id not in [f["id"] for f in resp.json()]

    resp = await client.post(
        "/api/v1/inquiries",
        json={"facility_id": facility_id, "message": "Interested in a tour", "contact_phone": "5551234567"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    resp = await client.get("/api/v1/inquiries/me", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = await client.post(
        "/api/v1/assessment/submit",
        json={"answers": {"primary_need": "memory_care"}},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["assessment"]["recommended_care_type"] == "Nursing Home / Skilled Nursing Facility"

    resp = await client.get("/api/v1/assessment/me/latest", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_guest_can_browse_but_inquiry_and_save_are_login_locked(client):
    """
    Product rule: guests can browse/search/view freely, but submitting an
    inquiry form or saving/liking a facility requires a real login -- the
    guest token must NOT work for these two write actions (the app shows a
    login prompt before ever reaching the API for them). Onboarding and
    assessment remain guest-accessible on purpose (not considered a
    "form submit" / "like" action for this rule).
    """
    resp = await client.post("/api/v1/auth/guest")
    assert resp.status_code == 200
    guest_token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {guest_token}"}

    # Browsing/search still works with no login at all.
    search_resp = await client.get("/api/v1/facilities/search?page=1&page_size=1")
    assert search_resp.status_code == 200
    facility_id = search_resp.json()["items"][0]["id"]

    # Inquiry submit is now login-locked -- guest token must be rejected.
    resp = await client.post(
        "/api/v1/inquiries",
        json={"facility_id": facility_id, "message": "Guest inquiry"},
        headers=headers,
    )
    assert resp.status_code == 401

    # Save/like is now login-locked -- guest token must be rejected.
    resp = await client.post(f"/api/v1/saved/{facility_id}", headers=headers)
    assert resp.status_code == 401

    resp = await client.get("/api/v1/profile/me", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonexistent_facility_returns_404(client):
    resp = await client.get("/api/v1/facilities/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_facility_id_returns_400(client):
    resp = await client.get("/api/v1/facilities/not-a-uuid")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resources_list_empty_ok(client):
    resp = await client.get("/api/v1/resources")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)