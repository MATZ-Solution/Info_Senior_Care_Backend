import asyncpg
import json
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from logger import log_db, log_error, log_success

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = None

async def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        log_error("DATABASE_URL not set — DB disabled")
        return
    try:
        log_db("Connecting to Supabase...")
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=2, max_size=10,
            command_timeout=60, statement_cache_size=0
        )
        log_success("Supabase connected!")
        await create_tables()
    except Exception as e:
        log_error(f"DB connection failed: {e}")
        db_pool = None

async def close_db_pool():
    global db_pool
    if db_pool:
        log_db("Closing DB pool...")
        await db_pool.close()
        log_db("DB pool closed")

@asynccontextmanager
async def get_db_connection():
    if not db_pool:
        raise RuntimeError("DB pool not initialized — check DATABASE_URL and Supabase connection")
    try:
        async with db_pool.acquire() as conn:
            yield conn
    except asyncpg.PostgresConnectionError as e:
        log_error(f"DB connection error: {e}")
        raise
    except asyncpg.PostgresError as e:
        log_error(f"DB query error: {e}")
        raise

async def create_tables():
    log_db("Creating/verifying tables...")
    async with get_db_connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_sessions (
                id SERIAL PRIMARY KEY,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT,
                title TEXT DEFAULT 'New Conversation',
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE infomary_sessions ADD COLUMN IF NOT EXISTS title TEXT DEFAULT 'New Conversation'")
        await conn.execute("ALTER TABLE infomary_sessions ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_messages (
                id SERIAL PRIMARY KEY,
                message_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (session_id) REFERENCES infomary_sessions(session_id) ON DELETE CASCADE
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON infomary_messages(session_id, created_at ASC)")
        await conn.execute("ALTER TABLE infomary_messages ADD COLUMN IF NOT EXISTS facility_cards JSONB")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_leads (
                id SERIAL PRIMARY KEY,
                lead_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                name TEXT DEFAULT '',
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                care_need TEXT DEFAULT '',
                care_type TEXT DEFAULT '',
                location TEXT DEFAULT '',
                age TEXT DEFAULT '',
                gender TEXT DEFAULT '',
                living_arrangement TEXT DEFAULT '',
                conditions TEXT DEFAULT '',
                insurance TEXT DEFAULT '',
                budget TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'New',
                email_sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_session ON infomary_leads(session_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_created ON infomary_leads(created_at DESC)")
        log_success("Tables created/verified!")

async def save_message(session_id: str, role: str, content: str, facility_cards: list | None = None):
    import uuid
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "INSERT INTO infomary_sessions (session_id) VALUES ($1) ON CONFLICT DO NOTHING",
                session_id
            )
            await conn.execute(
                "INSERT INTO infomary_messages (message_id, session_id, role, content, facility_cards) "
                "VALUES ($1, $2, $3, $4, $5::jsonb)",
                str(uuid.uuid4()), session_id, role, content,
                json.dumps(facility_cards) if facility_cards is not None else None
            )
    except Exception as e:
        log_error(f"save_message failed | session={session_id[:12]} | role={role} | {e}")
        raise

async def fetch_history(session_id: str):
    try:
        async with get_db_connection() as conn:
            rows = await conn.fetch(
                "SELECT role, content, facility_cards FROM infomary_messages WHERE session_id = $1 ORDER BY created_at ASC",
                session_id
            )
            return [
                {
                    "role": r["role"],
                    "content": r["content"],
                    "facility_cards": json.loads(r["facility_cards"]) if r["facility_cards"] else None,
                }
                for r in rows
            ]
    except Exception as e:
        log_error(f"fetch_history failed | session={session_id[:12]} | {e}")
        return []

async def update_session_title(session_id: str, title: str, description: str = ""):
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE infomary_sessions SET title = $1, description = $2 WHERE session_id = $3",
                title, description, session_id
            )
    except Exception as e:
        log_error(f"update_session_title failed | session={session_id[:12]} | {e}")

async def get_all_sessions():
    try:
        async with get_db_connection() as conn:
            rows = await conn.fetch(
                "SELECT session_id, title, description, created_at FROM infomary_sessions ORDER BY created_at DESC"
            )
            return [{"session_id": r["session_id"], "title": r["title"], "description": r["description"], "created_at": str(r["created_at"])} for r in rows]
    except Exception as e:
        log_error(f"get_all_sessions failed | {e}")
        return []

async def delete_session(session_id: str):
    try:
        async with get_db_connection() as conn:
            await conn.execute("DELETE FROM infomary_sessions WHERE session_id = $1", session_id)
    except Exception as e:
        log_error(f"delete_session failed | session={session_id[:12]} | {e}")
        raise

async def upsert_lead(lead: dict):
    lead_id = lead.get("lead_id", "?")
    if not db_pool:
        log_error(f"upsert_lead | db_pool not ready | lead={lead_id}")
        raise RuntimeError("DB pool not initialized")
    try:
        async with get_db_connection() as conn:
            await conn.execute("""
                INSERT INTO infomary_leads (
                    lead_id, session_id, name, email, phone,
                    care_need, care_type, location, age, gender,
                    living_arrangement, conditions, insurance, budget,
                    notes, status, email_sent, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,NOW())
                ON CONFLICT (lead_id) DO UPDATE SET
                    name = EXCLUDED.name, email = EXCLUDED.email, phone = EXCLUDED.phone,
                    care_need = EXCLUDED.care_need, care_type = EXCLUDED.care_type,
                    location = EXCLUDED.location, age = EXCLUDED.age, gender = EXCLUDED.gender,
                    living_arrangement = EXCLUDED.living_arrangement, conditions = EXCLUDED.conditions,
                    insurance = EXCLUDED.insurance, budget = EXCLUDED.budget,
                    notes = EXCLUDED.notes, status = EXCLUDED.status,
                    email_sent = EXCLUDED.email_sent, updated_at = NOW()
            """,
                lead.get("lead_id"), lead.get("session_id", ""),
                lead.get("name", ""), lead.get("email", ""), lead.get("phone", ""),
                lead.get("care_need", ""), lead.get("care_type", ""), lead.get("location", ""),
                lead.get("age", ""), lead.get("gender", ""),
                lead.get("living_arrangement", ""), lead.get("conditions", ""),
                lead.get("insurance", ""), lead.get("budget", ""),
                lead.get("notes", ""), lead.get("status", "New"),
                lead.get("email_sent", False),
            )
    except Exception as e:
        log_error(f"upsert_lead failed | lead={lead_id} | {e}")
        raise

async def get_dashboard_stats():
    try:
        async with get_db_connection() as conn:
            total_sessions = await conn.fetchval("SELECT COUNT(*) FROM infomary_sessions")
            total_leads    = await conn.fetchval("SELECT COUNT(*) FROM infomary_leads")
            qualified      = await conn.fetchval("SELECT COUNT(*) FROM infomary_leads WHERE name != '' AND (phone != '' OR email != '')")
            emails_sent    = await conn.fetchval("SELECT COUNT(*) FROM infomary_leads WHERE email_sent = TRUE")
            today_sessions = await conn.fetchval("SELECT COUNT(*) FROM infomary_sessions WHERE created_at >= NOW() - INTERVAL '24 hours'")
            today_leads    = await conn.fetchval("SELECT COUNT(*) FROM infomary_leads WHERE created_at >= NOW() - INTERVAL '24 hours'")
            trend = await conn.fetch("""
                SELECT DATE(created_at) as day, COUNT(*) as count
                FROM infomary_leads
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY day ORDER BY day ASC
            """)
            return {
                "total_sessions": total_sessions, "total_leads": total_leads,
                "qualified_leads": qualified, "emails_sent": emails_sent,
                "today_sessions": today_sessions, "today_leads": today_leads,
                "trend": [{"day": str(r["day"]), "count": r["count"]} for r in trend],
            }
    except Exception as e:
        log_error(f"get_dashboard_stats failed | {e}")
        raise

async def get_all_leads(limit: int = 100, offset: int = 0, status: str = None):
    try:
        async with get_db_connection() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM infomary_leads WHERE status = $1 ORDER BY updated_at DESC LIMIT $2 OFFSET $3",
                    status, limit, offset
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM infomary_leads ORDER BY updated_at DESC LIMIT $1 OFFSET $2",
                    limit, offset
                )
            return [dict(r) for r in rows]
    except Exception as e:
        log_error(f"get_all_leads failed | {e}")
        raise

async def update_lead_status(lead_id: str, status: str):
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE infomary_leads SET status=$1, updated_at=NOW() WHERE lead_id=$2",
                status, lead_id
            )
    except Exception as e:
        log_error(f"update_lead_status failed | lead={lead_id} | {e}")
        raise
