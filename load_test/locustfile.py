"""
Load test for the InfoSenior Care backend -- simulates realistic concurrent
user traffic to answer the question "will this survive 10,000 concurrent
users?" before relying on that number in production.

## What this simulates

Traffic is weighted to match how the real app is actually used, NOT an
even split across endpoints:
  - Search/browse (Home + Search screens) is by far the most common action
    -- most users search, browse results, and open a facility detail far
    more often than they save/inquire/submit an assessment.
  - Writes (save, inquiry, assessment) are deliberately rare in the mix,
    matching real usage -- and because hammering writes at full 10k
    concurrency against a free/small Supabase tier can exhaust its
    connection limits or hit usage caps. Start smaller and ramp up (see
    "How to run" below) rather than jumping straight to 10k.
  - A guest login happens once per simulated user (like a real app launch),
    not on every request.

## How to run

Install the extra dependency (kept separate from the app's own
requirements.txt so production installs don't pull in a load-testing tool):

    pip install -r requirements-loadtest.txt

Make sure the API server is actually running first (in another terminal):

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Then, from the `backend` directory:

    # Interactive web UI (recommended the first time -- lets you watch
    # requests/sec, response times, and failure rate live, and ramp up
    # gradually instead of slamming 10k users on instantly):
    locust -f load_test/locustfile.py --host http://localhost:8000

    Then open http://localhost:8089 in a browser, and enter:
      - Number of users: start with 100, then try 1000, then 10000
      - Spawn rate: e.g. 50 (users started per second) -- a gradual ramp-up
        is more realistic than an instant spike and easier to read on the
        charts.

    # OR headless (no browser UI), e.g. straight to 10k over 5 minutes:
    locust -f load_test/locustfile.py --host http://localhost:8000 \\
        --users 10000 --spawn-rate 50 --run-time 5m --headless \\
        --html load_test_report.html

## Reading the results

Watch for:
  - Failure rate -- should stay near 0%. Any spike means something (DB
    pool, Supabase connection limit, rate limiter) got overwhelmed.
  - p95/p99 response time on `facilities/search` -- this is the
    highest-traffic endpoint; if it climbs sharply as user count rises,
    the cache/DB isn't keeping up.
  - Watch your Supabase dashboard's connection count in parallel -- if it
    maxes out, you'll see connection errors here even though the app code
    itself is fine.

## IMPORTANT if testing against real Supabase

Running this at full 10k against a free-tier Supabase project can exhaust
its connection limits or trip usage-based throttling. Start at 100-500
users first and confirm the API/DB/cache hold up cleanly before ramping
toward 10k.
"""
import random

from locust import HttpUser, between, task

US_STATES = ["CA", "TX", "FL", "NY", "OH", "NC", "PA", "IL", "GA", "MI"]
CARE_TYPES = ["Nursing Home", "Home Health", "Hospice", "Assisted Living"]


class InfoSeniorCareUser(HttpUser):
    """
    One instance of this class = one simulated concurrent user. Locust
    spins up many of these (e.g. 10,000) each running its own loop of
    tasks with a random think-time between actions, mimicking real human
    pacing rather than a tight request-spam loop.
    """

    # Random pause between actions, like a real person reading a screen
    # before tapping the next thing -- an important realism detail, since
    # zero-wait loops would generate far more load than 10k real users ever
    # would and give a misleadingly pessimistic result.
    wait_time = between(1, 4)

    def on_start(self):
        """Runs once per simulated user at the start -- like an app launch."""
        self.known_facility_ids = []
        self._guest_login()

    def _guest_login(self):
        resp = self.client.post("/api/v1/auth/guest", name="/auth/guest")
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    def _remember_facility_ids(self, response):
        try:
            items = response.json().get("items") or response.json()
            if isinstance(items, list):
                for item in items:
                    fid = item.get("id")
                    if fid:
                        self.known_facility_ids.append(fid)
                        # cap memory per simulated user
                        self.known_facility_ids = self.known_facility_ids[-20:]
        except (ValueError, AttributeError):
            pass

    # ---- Read-heavy tasks (the vast majority of real traffic) ----

    @task(30)
    def search_facilities(self):
        params = {"page": random.randint(1, 3), "page_size": 20}
        if random.random() < 0.6:
            params["state"] = random.choice(US_STATES)
        if random.random() < 0.3:
            params["facility_type"] = random.choice(CARE_TYPES)

        resp = self.client.get(
            "/api/v1/facilities/search", params=params, name="/facilities/search"
        )
        if resp.status_code == 200:
            self._remember_facility_ids(resp)

    @task(15)
    def view_facility_detail(self):
        if not self.known_facility_ids:
            return  # haven't searched yet this cycle -- skip rather than fail
        facility_id = random.choice(self.known_facility_ids)
        self.client.get(f"/api/v1/facilities/{facility_id}", name="/facilities/[id]")

    @task(10)
    def suggest_autocomplete(self):
        query = random.choice(["nursing", "home", "care", "senior", "health", "manor"])
        self.client.get(
            "/api/v1/facilities/suggest", params={"q": query, "limit": 8}, name="/facilities/suggest"
        )

    @task(8)
    def recommended(self):
        params = {"limit": 10}
        if random.random() < 0.5:
            params["state"] = random.choice(US_STATES)
        self.client.get("/api/v1/facilities/recommended", params=params, name="/facilities/recommended")

    @task(5)
    def browse_resources(self):
        self.client.get("/api/v1/resources", name="/resources")

    @task(3)
    def health_check(self):
        # Real traffic wouldn't hit this, but load balancers poll it
        # constantly in production -- worth including at low weight.
        self.client.get("/health", name="/health")

    # ---- Write tasks (deliberately rare -- see module docstring) ----

    @task(2)
    def save_a_facility(self):
        if not self.known_facility_ids:
            return
        facility_id = random.choice(self.known_facility_ids)
        self.client.post(f"/api/v1/saved/{facility_id}", name="/saved/[id] (POST)")

    @task(1)
    def submit_inquiry(self):
        if not self.known_facility_ids:
            return
        facility_id = random.choice(self.known_facility_ids)
        self.client.post(
            "/api/v1/inquiries",
            json={"facility_id": facility_id, "message": "Load test inquiry -- please ignore"},
            name="/inquiries (POST)",
        )

    @task(1)
    def submit_assessment(self):
        self.client.post(
            "/api/v1/assessment/submit",
            json={"answers": {"primary_need": random.choice(
                ["memory_care", "independent", "medical_support", "end_of_life"]
            )}},
            name="/assessment/submit (POST)",
        )
