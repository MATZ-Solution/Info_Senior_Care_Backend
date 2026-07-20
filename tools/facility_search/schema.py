"""
Phase 1 — Supabase schema for the facility search data foundation.

Creates the lookup/config tables (infomary_facility_types, infomary_facility_type_aliases,
infomary_known_values, infomary_source_field_mappings) and the two data layers
(infomary_facilities, infomary_facility_detail) described in the architecture docs.
No ETL, embedding, or Qdrant work happens here — schema only.
"""
from database import get_db_connection
from logger import log_db, log_success


async def create_tables():
    log_db("Creating/verifying facility search tables...")
    async with get_db_connection() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_facility_types (
                id SERIAL PRIMARY KEY,
                type_key TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_facility_type_aliases (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                type_key TEXT NOT NULL REFERENCES infomary_facility_types(type_key) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                UNIQUE (type_key, alias)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facility_type_aliases_trgm "
            "ON infomary_facility_type_aliases USING gin (alias gin_trgm_ops)"
        )

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_known_values (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                field TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE (field, value)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_known_values_trgm "
            "ON infomary_known_values USING gin (value gin_trgm_ops)"
        )

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_source_field_mappings (
                id SERIAL PRIMARY KEY,
                source_table TEXT NOT NULL,
                source_column TEXT NOT NULL,
                target_field TEXT NOT NULL,
                target_layer TEXT NOT NULL CHECK (target_layer IN ('facilities', 'facility_detail')),
                transform_fn TEXT,
                is_required BOOLEAN NOT NULL DEFAULT FALSE,
                facility_type TEXT NOT NULL REFERENCES infomary_facility_types(type_key),
                UNIQUE (source_table, source_column, target_field)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_facilities (
                facility_id UUID PRIMARY KEY,
                facility_type TEXT NOT NULL REFERENCES infomary_facility_types(type_key),
                name TEXT NOT NULL,
                address_line1 TEXT,
                address_line2 TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                county TEXT,
                phone TEXT,
                ownership_type TEXT,
                certification_date DATE,
                cms_region SMALLINT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                source_identifier TEXT,
                source_table TEXT NOT NULL,
                source_row_hash TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_type ON infomary_facilities (facility_type)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_state ON infomary_facilities (state)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_city ON infomary_facilities (city)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_ownership ON infomary_facilities (ownership_type)")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_facility_detail (
                facility_id UUID PRIMARY KEY REFERENCES infomary_facilities(facility_id) ON DELETE CASCADE,
                attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
                schema_version SMALLINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facility_detail_attrs "
            "ON infomary_facility_detail USING gin (attributes jsonb_path_ops)"
        )

        # Phase 3 -- tracks what was last pushed to Qdrant per facility, keyed by a
        # hash of the *generated embedding text* (not source_row_hash), so a
        # template-only change (no underlying data change) still triggers
        # re-embedding on the next embed_sync run.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infomary_facility_embeddings (
                facility_id UUID PRIMARY KEY REFERENCES infomary_facilities(facility_id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                vector_dimensions SMALLINT NOT NULL,
                embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # updated_at only fires on INSERT via the column default — these triggers keep it
        # honest on UPDATE too, since the Qdrant re-sync design (docs, Section 3.3/11) relies
        # on updated_at/source_row_hash to detect what changed.
        await conn.execute("""
            CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
            BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
            $$ LANGUAGE plpgsql
        """)
        await conn.execute("""
            DROP TRIGGER IF EXISTS infomary_facilities_set_updated_at ON infomary_facilities
        """)
        await conn.execute("""
            CREATE TRIGGER infomary_facilities_set_updated_at
            BEFORE UPDATE ON infomary_facilities
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """)
        await conn.execute("""
            DROP TRIGGER IF EXISTS infomary_facility_detail_set_updated_at ON infomary_facility_detail
        """)
        await conn.execute("""
            CREATE TRIGGER infomary_facility_detail_set_updated_at
            BEFORE UPDATE ON infomary_facility_detail
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """)

        log_success("Facility search tables created/verified!")
