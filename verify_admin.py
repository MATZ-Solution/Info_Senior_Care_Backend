import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv(".env")

async def main():
    url = os.getenv("DATABASE_URL","").replace("+asyncpg","")
    c = await asyncpg.connect(url, statement_cache_size=0)

    # 1. inquiries table ke saare naye columns maujood hain?
    cols = [r["column_name"] for r in await c.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name='inquiries'")]
    need = ["user_name","user_email","facility_name","facility_type_category",
            "state","city","budget","message","contact_time_preference"]
    print("=== inquiries columns ===")
    for col in need:
        print(f"  {'OK ' if col in cols else 'MISSING'} {col}")

    # 2. alembic head sahi hai?
    ver = await c.fetchval("SELECT version_num FROM alembic_version")
    print("\n=== alembic version ===\n  ", ver, "(a6d4e5f7b3c2 = latest)")

    # 3. dono lead tables mein data
    inq = await c.fetchval("SELECT count(*) FROM inquiries")
    lead = await c.fetchval("SELECT count(*) FROM infomary_leads")
    print(f"\n=== data ===\n   inquiries (form leads): {inq}\n   infomary_leads (chat) : {lead}")

    await c.close()

asyncio.run(main())
