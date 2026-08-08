import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv(".env")
async def main():
    url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    c = await asyncpg.connect(url)
    total = await c.fetchval("SELECT count(*) FROM inquiries")
    print("Total inquiries saved:", total)
    rows = await c.fetch("SELECT id, facility_id, contact_phone, contact_time_preference, status, created_at FROM inquiries ORDER BY created_at DESC LIMIT 5")
    for r in rows:
        print(dict(r))
    await c.close()
asyncio.run(main())
