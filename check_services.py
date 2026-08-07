import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv(".env")

OFFERS = [
    "offers_alzheimer_dementia_care","offers_hospice_care","offers_ventilator_care",
    "offers_psychiatric_care","offers_rehab_services","offers_adult_day_care",
    "offers_respite_care","offers_home_care_services","offers_traumatic_brain_injury_care",
]

async def main():
    url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    c = await asyncpg.connect(url)
    total = await c.fetchval("SELECT count(*) FROM facility_services")
    print("facility_services rows:", total)
    if total == 0:
        print(">>> Table EMPTY — import ne services populate nahi kiye (CSV column-name mismatch).")
    for col in OFFERS:
        nn = await c.fetchval(f"SELECT count({col}) FROM facility_services")
        vals = await c.fetch(
            f"SELECT DISTINCT {col} v FROM facility_services "
            f"WHERE {col} IS NOT NULL LIMIT 6")
        sample = ", ".join(repr(r["v"]) for r in vals)
        print(f"  {col:<38} non-null={nn:<7} values: {sample}")
    await c.close()

asyncio.run(main())
