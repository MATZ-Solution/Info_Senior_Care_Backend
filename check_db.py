import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv(".env")

TABLES = ["facilities", "nursing_home_details", "home_health_details", "facility_services"]

async def main():
    url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    c = await asyncpg.connect(url)
    for t in TABLES:
        cols = await c.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=$1 ORDER BY ordinal_position", t)
        print(f"\n=== {t} ({len(cols)} columns) ===")
        print(", ".join(r["column_name"] for r in cols))
    print("\n=== facility_type_category breakdown ===")
    rows = await c.fetch(
        "SELECT facility_type_category, count(*) c "
        "FROM facilities GROUP BY 1 ORDER BY c DESC")
    for r in rows:
        print(f"  {r['c']:>7}  {r['facility_type_category']}")
    await c.close()

asyncio.run(main())
