import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv(".env")

async def main():
    url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    c = await asyncpg.connect(url)
    # cross-check: do Hospice-category facilities actually flag hospice care?
    rows = await c.fetch("""
        SELECT s.offers_hospice_care AS v, count(*) c
        FROM facility_services s
        JOIN facilities f ON f.id = s.facility_id
        WHERE f.facility_type_category = 'Nursing Home / Skilled Nursing Facility'
        GROUP BY 1 ORDER BY c DESC LIMIT 8
    """)
    print("Nursing homes -> offers_hospice_care distribution:")
    for r in rows: print(f"   {r['c']:>6}  {r['v']!r}")

    # overall value-shape per column: how many look boolean vs numeric
    for col in ["offers_adult_day_care","offers_alzheimer_dementia_care","offers_hospice_care"]:
        tf = await c.fetchval(f"SELECT count(*) FROM facility_services WHERE {col} IN ('T','F')")
        num = await c.fetchval(f"SELECT count(*) FROM facility_services WHERE {col} ~ '^[0-9.]+$'")
        print(f"{col:<38} T/F={tf:<6} numeric={num}")
    await c.close()

asyncio.run(main())
