# #!/usr/bin/env python
# """
# TypeSense integration doctor — the single script that checks everything.

#     python scripts/typesense_doctor.py

# Runs 9 checks in dependency order and prints exactly what to do next. The order
# matters: a failure early on makes everything after it meaningless, so the
# script stops rather than burying the real problem under a wall of noise.

# READ-ONLY. Creates nothing, imports nothing, deletes nothing. Safe to run
# against production at any time.

# Exit codes: 0 ready · 1 something is broken · 2 could not run.
# """
# from __future__ import annotations

# import argparse
# import asyncio
# import sys
# from collections import Counter
# from pathlib import Path
# from typing import Any

# # Make `import app...` work regardless of how this file is invoked.
# #
# # `python -m scripts.typesense_doctor` puts the project root on sys.path, but
# # `python scripts/typesense_doctor.py` puts `scripts/` there instead — and then
# # every `from app.core...` fails with ModuleNotFoundError. A diagnostic tool
# # should not be sensitive to how it was started, so the root is added here.
# _PROJECT_ROOT = Path(__file__).resolve().parent.parent
# if str(_PROJECT_ROOT) not in sys.path:
#     sys.path.insert(0, str(_PROJECT_ROOT))

# # --------------------------------------------------------------------------
# # Output
# # --------------------------------------------------------------------------

# _COLOR = sys.stdout.isatty()
# G = "\033[32m" if _COLOR else ""
# R = "\033[31m" if _COLOR else ""
# Y = "\033[33m" if _COLOR else ""
# B = "\033[1m" if _COLOR else ""
# X = "\033[0m" if _COLOR else ""

# _problems: list[str] = []


# def section(title: str) -> None:
#     print(f"\n{B}{title}{X}")
#     print("-" * 62)


# def ok(msg: str) -> None:
#     print(f"  {G}PASS{X}  {msg}")


# def fail(msg: str, fix: str = "") -> None:
#     print(f"  {R}FAIL{X}  {msg}")
#     if fix:
#         print(f"        {Y}fix:{X} {fix}")
#         _problems.append(f"{msg}\n         fix: {fix}")
#     else:
#         _problems.append(msg)


# def warn(msg: str) -> None:
#     print(f"  {Y}WARN{X}  {msg}")


# def info(msg: str) -> None:
#     print(f"        {msg}")


# # --------------------------------------------------------------------------
# # 1. Dependency
# # --------------------------------------------------------------------------

# EXPECTED_TYPESENSE_VERSION = "1.1.1"


# def check_dependency() -> bool:
#     section("1. Dependency")
#     try:
#         import typesense  # noqa: F401
#     except ImportError:
#         fail(
#             "the `typesense` package is not installed",
#             f"pip install typesense=={EXPECTED_TYPESENSE_VERSION}",
#         )
#         return False

#     try:
#         from importlib.metadata import version as pkg_version

#         installed = pkg_version("typesense")
#     except Exception:  # noqa: BLE001
#         installed = "unknown"

#     if installed != EXPECTED_TYPESENSE_VERSION:
#         warn(f"typesense {installed} installed; this code was verified against {EXPECTED_TYPESENSE_VERSION}")
#     else:
#         ok(f"typesense {installed}")
#     return True


# # --------------------------------------------------------------------------
# # 2. Module integrity (functional, not cosmetic)
# # --------------------------------------------------------------------------


# def check_modules() -> bool:
#     """
#     Verify the code that ACTUALLY RUNS, not what the files look like.

#     This deliberately does not compare line counts or diff against a reference
#     copy: comments get edited, files get reformatted, and a cosmetic mismatch
#     is not a defect. What matters is whether the objects Python ends up with
#     behave correctly.

#     That also happens to catch the stale/duplicated-file problem for free. If
#     an older revision is still present — appended below, or commented out and
#     partially restored — Python resolves the LAST definition, so importing the
#     symbol and exercising it reports on the copy that is really in effect.
#     """
#     section("2. Module integrity")
#     healthy = True

#     def probe(label: str, fn) -> bool:
#         nonlocal healthy
#         try:
#             detail = fn()
#         except ModuleNotFoundError as exc:
#             missing = (exc.name or "").split(".")[0]
#             if missing in {"app", "scripts"}:
#                 fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
#             else:
#                 # A third-party package is absent. Saying "stale file" here
#                 # would send the operator editing perfectly good code.
#                 fail(f"{label} — required package {missing!r} is not installed",
#                      f"pip install {missing}   (or: pip install -r requirements.txt)")
#             healthy = False
#             return False
#         except ImportError as exc:
#             fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
#             healthy = False
#             return False
#         except AssertionError as exc:
#             fail(f"{label} — {exc}", "an older revision of this file is what is actually running")
#             healthy = False
#             return False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label} — {type(exc).__name__}: {exc}")
#             healthy = False
#             return False
#         ok(f"{label}{f' — {detail}' if detail else ''}")
#         return True

#     # ---- config ----
#     def _config():
#         from app.core.config import settings

#         for attr in ("TYPESENSE_ADMIN_API_KEY", "TYPESENSE_COLLECTION", "TYPESENSE_SEARCH_ENABLED"):
#             assert hasattr(settings, attr), f"settings is missing {attr}"
#         assert hasattr(settings, "typesense_nodes"), "settings is missing the typesense_nodes property"
#         assert isinstance(settings.typesense_nodes, list), "typesense_nodes must return a list"
#         return "settings expose the TypeSense config"

#     probe("app.core.config", _config)

#     # ---- client ----
#     def _client():
#         from app.core import typesense as mod

#         for name in (
#             "use_admin_credentials",
#             "get_typesense_client",
#             "run_typesense",
#             "check_typesense_connection",
#             "is_configured",
#             "TypesenseUnavailable",
#         ):
#             assert hasattr(mod, name), f"`{name}` is missing"
#         # Confirm the health check makes an AUTHENTICATED call. Checked via the
#         # AST rather than a substring, because the current docstring mentions
#         # `operations.is_healthy` precisely to explain why it is NOT used — a
#         # plain `in source` test matches that prose and reports a false failure.
#         #
#         # Collects every attribute EXPRESSION, not just direct calls: these
#         # operations are handed to `run_typesense(client.collections.retrieve)`
#         # as references, so the attribute is an argument rather than the
#         # callee, and a call-only walk misses it entirely.
#         import ast as _ast
#         import inspect
#         import textwrap

#         tree = _ast.parse(textwrap.dedent(inspect.getsource(mod.check_typesense_connection)))
#         referenced = {
#             _ast.unparse(node)
#             for node in _ast.walk(tree)
#             if isinstance(node, _ast.Attribute)
#         }
#         assert not any(r.endswith("operations.is_healthy") for r in referenced), (
#             "the health check uses operations.is_healthy, which is UNAUTHENTICATED "
#             "and returns true even for a wrong key"
#         )
#         assert any("collections" in r for r in referenced), (
#             "the health check makes no authenticated call"
#         )
#         return "admin-credential switch + authenticated health check present"

#     probe("app.core.typesense", _client)

#     # ---- collection schema ----
#     def _collection():
#         from app.services.typesense_collection_service import (
#             FIELD_NUM_TYPOS,
#             INFIX_FIELDS,
#             QUERY_BY_FIELDS,
#             QUERY_BY_WEIGHTS,
#             build_schema,
#         )

#         schema = build_schema()
#         fields = {f["name"] for f in schema["fields"]}

#         assert schema["default_sorting_field"] == "rating_sort", "default_sorting_field is wrong"
#         assert "rating_sort" in fields, "rating_sort field missing"
#         assert not any(
#             f["name"] == "rating_sort" and f.get("optional") for f in schema["fields"]
#         ), "rating_sort must NOT be optional — TypeSense rejects the collection otherwise"
#         assert "zip_code" in QUERY_BY_FIELDS, "zip_code is not searchable"
#         assert len(QUERY_BY_WEIGHTS) == len(QUERY_BY_FIELDS), "weights/fields arity mismatch"
#         assert FIELD_NUM_TYPOS.get("state") == 0, "state must not tolerate typos (CA vs GA)"
#         assert FIELD_NUM_TYPOS.get("zip_code") == 0, "zip_code must not tolerate typos"
#         declared_infix = {f["name"] for f in schema["fields"] if f.get("infix")}
#         assert INFIX_FIELDS == declared_infix, "INFIX_FIELDS disagrees with the schema"
#         assert set(QUERY_BY_FIELDS) <= fields, "a searched field is not in the schema"
#         return f"{len(fields)} fields, searchable: {', '.join(QUERY_BY_FIELDS)}"

#     probe("app.services.typesense_collection_service", _collection)

#     # ---- mapper ----
#     def _mapper():
#         from app.utils.facility_mapper import build_select_sql, to_document

#         doc = to_document(
#             {
#                 "uuid": "abc",
#                 "name": "TEST FACILITY",
#                 "city": "NAPA",
#                 "state": " ca ",
#                 "ownership_type": "unknown",
#                 "latitude": "38.3",
#                 "longitude": "",
#                 "bed_count": "1,200",
#                 "updated_at": "2026-07-22 09:39:30",
#             }
#         )
#         assert doc["state"] == "CA", "state is not upper-cased"
#         assert "ownership_type" not in doc, "'unknown' was not normalised away"
#         assert "latitude" not in doc, "a lone coordinate was kept"
#         assert doc["bed_count"] == 1200, "thousands separator not handled"
#         assert doc["rating_sort"] == 0.0, "rating_sort not always emitted"
#         assert None not in doc.values(), "explicit None emitted — TypeSense rejects those"

#         sql = build_select_sql()
#         assert "OFFSET" not in sql.upper(), "still using OFFSET pagination"
#         assert ":after_uuid" in sql and ":since" in sql, "keyset/incremental params missing"
#         assert 'public."All_State_Type_combined"' in sql, "source table not quoted correctly"
#         return "coercion, null handling and keyset SQL all correct"

#     probe("app.utils.facility_mapper", _mapper)

#     # ---- search service ----
#     def _search():
#         from app.services.typesense_search_service import build_search_params

#         params = build_search_params(
#             q="94559", name=None, city=None, state="CA", zip_code=None,
#             facility_type=None, facility_type_category=None, page=1, page_size=20,
#         )
#         arities = {len(params[k].split(",")) for k in ("query_by", "query_by_weights", "num_typos", "infix")}
#         assert len(arities) == 1, f"per-field parameter arity mismatch: {arities}"
#         assert params["num_typos"].endswith("0"), "zip_code typo tolerance is not 0"
#         assert "filter_by" in params and "state:=" in params["filter_by"], "state filter not applied"
#         assert None not in params.values(), "None value would be rejected by TypeSense"
#         return f"query_by={params['query_by']} num_typos={params['num_typos']}"

#     probe("app.services.typesense_search_service", _search)

#     # ---- sync service ----
#     def _sync():
#         from app.services import typesense_sync_service as mod

#         for name in (
#             "run_full_sync",
#             "run_incremental_sync",
#             "reconcile_deletions",
#             "verify_sync",
#             "get_index_watermark",
#         ):
#             assert hasattr(mod, name), f"`{name}` is missing"
#         return "full / incremental / reconcile / verify all present"

#     probe("app.services.typesense_sync_service", _sync)

#     # ---- import CLI ----
#     def _cli():
#         from scripts import typesense_import as mod

#         assert hasattr(mod, "main"), "`main` is missing"
#         return None

#     probe("scripts.typesense_import", _cli)

#     return healthy


# # --------------------------------------------------------------------------
# # 3. Environment
# # --------------------------------------------------------------------------


# def check_env() -> bool:
#     section("3. Environment")
#     try:
#         from app.core.config import settings
#     except ModuleNotFoundError as exc:
#         # Not an environment problem at all — the project root is not on
#         # sys.path, or a package is missing. Saying "check .env" here would
#         # send the operator hunting in entirely the wrong file.
#         fail(
#             f"could not import the app package — {exc}",
#             f"run from the project root ({_PROJECT_ROOT}), "
#             "and make sure the virtualenv is active",
#         )
#         return False
#     except Exception as exc:  # noqa: BLE001
#         fail(
#             f"could not load settings — {type(exc).__name__}: {exc}",
#             "check .env for lines python-dotenv could not parse, "
#             "and for a required variable that is missing",
#         )
#         return False

#     healthy = True

#     nodes = settings.typesense_nodes
#     if not nodes:
#         fail("no TypeSense nodes configured", "set TYPESENSE_HOST (or TYPESENSE_NODES) in .env")
#         healthy = False
#     else:
#         rendered = ", ".join(f"{n['protocol']}://{n['host']}:{n['port']}" for n in nodes)
#         ok(f"nodes: {rendered}")

#     def inspect_key(label: str, value: str, required: bool) -> bool:
#         if not value:
#             if required:
#                 fail(f"{label} is EMPTY", "set it in .env — check the dotenv parse warnings")
#                 return False
#             warn(f"{label} not set — the import will fall back to the search key and likely 401")
#             return True
#         if value != value.strip():
#             fail(f"{label} has surrounding whitespace", "remove spaces/newlines around the value")
#             return False
#         if value[0] in "\"'" or value[-1] in "\"'":
#             fail(f"{label} has stray quotes", "write it unquoted in .env")
#             return False
#         ok(f"{label}: set ({len(value)} chars, ends {value[-4:]!r})")
#         return True

#     if not inspect_key("TYPESENSE_API_KEY (search)", settings.TYPESENSE_API_KEY, True):
#         healthy = False
#     if not inspect_key("TYPESENSE_ADMIN_API_KEY", settings.TYPESENSE_ADMIN_API_KEY, False):
#         healthy = False

#     if settings.TYPESENSE_API_KEY and settings.TYPESENSE_API_KEY == settings.TYPESENSE_ADMIN_API_KEY:
#         warn("both keys are identical — the running app should use a search-only key")

#     ok(f"collection: {settings.TYPESENSE_COLLECTION}")
#     if not settings.TYPESENSE_SEARCH_ENABLED:
#         warn("TYPESENSE_SEARCH_ENABLED=false — the app will serve search from Postgres")

#     return healthy


# # --------------------------------------------------------------------------
# # 4. API keys
# # --------------------------------------------------------------------------


# def check_keys() -> bool:
#     """
#     Authenticate each key separately and report its real capability.

#     Uses an authenticated call, deliberately NOT `operations.is_healthy`: that
#     maps to TypeSense's `GET /health`, which is UNAUTHENTICATED and returns
#     true for a wrong, revoked or empty key. A check built on it reports
#     success, then the next call 401s — pointing the operator at the network
#     when the problem is the key.
#     """
#     section("4. API keys")
#     import typesense
#     from typesense.exceptions import ObjectNotFound, RequestForbidden, RequestUnauthorized

#     from app.core.config import settings

#     def build(api_key: str) -> typesense.Client:
#         return typesense.Client(
#             {
#                 "nodes": settings.typesense_nodes,
#                 "api_key": api_key,
#                 "connection_timeout_seconds": 8.0,
#                 "num_retries": 1,
#             }
#         )

#     healthy = True
#     admin_key = settings.TYPESENSE_ADMIN_API_KEY or settings.TYPESENSE_API_KEY

#     for label, api_key, needs_admin in (
#         ("ADMIN key ", admin_key, True),
#         ("SEARCH key", settings.TYPESENSE_API_KEY, False),
#     ):
#         if not api_key:
#             warn(f"{label}: not set, skipped")
#             continue

#         client = build(api_key)

#         try:
#             collections = client.collections.retrieve()
#             names = [c["name"] for c in collections]
#             ok(f"{label}: ADMIN access — collections: {names or '(none yet)'}")
#             continue
#         except (RequestUnauthorized, RequestForbidden):
#             if needs_admin:
#                 fail(
#                     f"{label}: 401/403 — this key has no admin rights",
#                     "put the Admin API Key (not the Search Only key) in TYPESENSE_ADMIN_API_KEY",
#                 )
#                 healthy = False
#                 continue
#         except Exception as exc:  # noqa: BLE001
#             fail(
#                 f"{label}: cannot reach TypeSense — {type(exc).__name__}: {exc}",
#                 "check the host/port in .env and that the cluster is running",
#             )
#             healthy = False
#             continue

#         try:
#             client.collections[settings.TYPESENSE_COLLECTION].documents.search(
#                 {"q": "*", "query_by": "name", "per_page": 1}
#             )
#             ok(f"{label}: SEARCH access on {settings.TYPESENSE_COLLECTION!r}")
#         except ObjectNotFound:
#             ok(f"{label}: authenticated (collection not created yet — expected before first import)")
#         except (RequestUnauthorized, RequestForbidden):
#             fail(
#                 f"{label}: 401/403 — invalid, revoked, or scoped to another collection",
#                 "regenerate the key in the TypeSense Cloud dashboard",
#             )
#             healthy = False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label}: {type(exc).__name__}: {exc}")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # 5. Source table
# # --------------------------------------------------------------------------


# async def check_source_table() -> bool:
#     section("5. Postgres source table")
#     from sqlalchemy import text

#     from app.core.database import AsyncSessionLocal
#     from app.utils.facility_mapper import SOURCE_COLUMNS, SOURCE_TABLE

#     try:
#         async with AsyncSessionLocal() as session:
#             result = await session.execute(
#                 text(
#                     "SELECT column_name FROM information_schema.columns "
#                     "WHERE table_schema = 'public' AND table_name = 'All_State_Type_combined'"
#                 )
#             )
#             available = {row[0] for row in result.all()}

#             if not available:
#                 fail(
#                     f"{SOURCE_TABLE} not found or not readable",
#                     "check the table name, schema, and database credentials",
#                 )
#                 return False
#             ok(f"{SOURCE_TABLE} found ({len(available)} columns)")

#             missing = [col for col in SOURCE_COLUMNS if col not in available]
#             if missing:
#                 fail(
#                     f"columns the mapper needs are missing: {', '.join(missing)}",
#                     "the source schema changed — update SOURCE_COLUMNS in facility_mapper.py",
#                 )
#                 return False
#             ok(f"all {len(SOURCE_COLUMNS)} mapped columns present")

#             count = (
#                 await session.execute(text('SELECT COUNT(*) FROM public."All_State_Type_combined"'))
#             ).scalar_one()
#             ok(f"{count:,} rows")

#             nulls = (
#                 await session.execute(
#                     text(
#                         'SELECT COUNT(*) FROM public."All_State_Type_combined" '
#                         'WHERE "updated_at" IS NULL'
#                     )
#                 )
#             ).scalar_one()
#             if nulls:
#                 warn(f"{nulls:,} rows have NULL updated_at — incremental sync will always skip them")
#             else:
#                 ok("updated_at populated on every row — incremental sync will work")
#             return True
#     except Exception as exc:  # noqa: BLE001
#         fail(
#             f"cannot query Postgres — {type(exc).__name__}: {exc}",
#             "check DATABASE_URL / ASYNC_DATABASE_URL",
#         )
#         return False


# # --------------------------------------------------------------------------
# # 6. Mapper
# # --------------------------------------------------------------------------


# async def check_mapper() -> bool:
#     """Map REAL rows, so the check reflects the actual data rather than a fixture."""
#     section("6. Mapper (against real rows)")
#     from sqlalchemy import text

#     from app.core.database import AsyncSessionLocal
#     from app.utils.facility_mapper import build_select_sql, to_documents

#     try:
#         async with AsyncSessionLocal() as session:
#             result = await session.execute(
#                 text(build_select_sql()), {"after_uuid": None, "since": None, "limit": 200}
#             )
#             rows = [dict(row) for row in result.mappings()]
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not read sample rows — {type(exc).__name__}: {exc}")
#         return False

#     if not rows:
#         fail("the source table returned no rows")
#         return False

#     documents, errors = to_documents(rows)
#     ok(f"mapped {len(documents)}/{len(rows)} sample rows")

#     if errors:
#         warn(f"{len(errors)} rows unmappable (no uuid or no name) — they will not be searchable")
#         for message in errors[:3]:
#             info(message)

#     if not documents:
#         fail("no rows produced a valid document")
#         return False

#     coverage: Counter[str] = Counter()
#     for doc in documents:
#         coverage.update(doc.keys())
#     total = len(documents)

#     info("field coverage across the sample:")
#     for field in (
#         "name", "city", "state", "zip_code", "address",
#         "facility_type_category", "ownership_type",
#         "latitude", "overall_rating", "bed_count",
#     ):
#         pct = coverage.get(field, 0) / total * 100
#         info(f"  {field:<24} {pct:5.1f}%  {'#' * int(pct / 5)}")

#     sample = documents[0]
#     info(f"sample: {sample.get('name')!r} — {sample.get('city')}, {sample.get('state')}")
#     return True


# # --------------------------------------------------------------------------
# # 7. Collection
# # --------------------------------------------------------------------------


# async def check_collection() -> tuple[bool, int]:
#     section("7. TypeSense collection")
#     from app.core.config import settings
#     from app.core.typesense import get_typesense_client, run_typesense, use_admin_credentials
#     from app.services.typesense_collection_service import build_schema, get_collection_stats

#     # This process only reads, but reading the collection schema is itself an
#     # admin operation.
#     use_admin_credentials()

#     try:
#         stats = await get_collection_stats()
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not read the collection — {type(exc).__name__}: {exc}")
#         return False, 0

#     if stats is None:
#         warn(f"collection {settings.TYPESENSE_COLLECTION!r} does not exist yet")
#         info("expected before the first import")
#         return True, 0

#     ok(f"collection {stats['name']!r} exists")
#     ok(f"{stats['num_documents']:,} documents indexed")

#     expected_fields = {f["name"] for f in build_schema()["fields"]}
#     client = get_typesense_client()
#     live = await run_typesense(client.collections[settings.TYPESENSE_COLLECTION].retrieve)
#     live_fields = {f["name"] for f in live.get("fields", [])}

#     missing = expected_fields - live_fields
#     if missing:
#         fail(
#             f"the live collection is missing fields: {', '.join(sorted(missing))}",
#             "schema changed — run: python -m scripts.typesense_import --full --recreate",
#         )
#         return False, stats["num_documents"]

#     ok(f"schema matches ({len(expected_fields)} fields)")
#     return True, stats["num_documents"]


# # --------------------------------------------------------------------------
# # 8. API wiring
# # --------------------------------------------------------------------------


# def check_api_wiring() -> bool:
#     """
#     Verify the endpoint file is wired correctly.

#     Two failure modes here are silent rather than loud:

#     * A duplicate route — FastAPI keeps the FIRST registration, so a leftover
#       old handler wins, the TypeSense path is never called, and nothing errors.
#     * "/{facility_id}" declared before "/search" — the catch-all swallows the
#       literal paths and every search 400s with "Invalid facility id".
#     """
#     section("8. API wiring")
#     try:
#         from app.api.v1.endpoints.facilities import router
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not import the facilities endpoint — {type(exc).__name__}: {exc}")
#         return False

#     routes = [(r.path, tuple(sorted(r.methods))) for r in router.routes]
#     healthy = True

#     duplicates = [path for path, count in Counter(routes).items() if count > 1]
#     if duplicates:
#         fail(
#             f"duplicate routes registered: {duplicates}",
#             "an old copy of a handler is still in facilities.py — delete it",
#         )
#         healthy = False
#     else:
#         ok(f"{len(routes)} routes, no duplicates")

#     paths = [path for path, _ in routes]
#     if "/{facility_id}" in paths and paths[-1] != "/{facility_id}":
#         fail(
#             "'/{facility_id}' is not declared last — it will swallow /search and /suggest",
#             "move get_facility_detail to the bottom of facilities.py",
#         )
#         healthy = False
#     else:
#         ok("catch-all '/{facility_id}' is declared last")

#     for required in ("/search", "/suggest"):
#         if required not in paths:
#             fail(f"route {required} is not registered")
#             healthy = False

#     import inspect

#     from app.api.v1.endpoints import facilities as module

#     source = inspect.getsource(module)
#     for marker, label in (
#         ("typesense_search_service", "TypeSense wired into the endpoint"),
#         ("_search_facilities_postgres", "Postgres fallback present"),
#         ("TypesenseUnavailable", "fallback exception handling present"),
#     ):
#         if marker in source:
#             ok(label)
#         else:
#             fail(f"{label} — NOT found in facilities.py", "replace it with the final version")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # 9. Live search
# # --------------------------------------------------------------------------


# async def check_live_search(document_count: int) -> bool:
#     section("9. Live search")
#     if document_count == 0:
#         warn("index is empty — skipping (run the import first)")
#         return True

#     from app.services import typesense_search_service as svc

#     queries: list[tuple[str, dict[str, Any]]] = [
#         ("filters only (browse)", {"state": "CA"}),
#         ("city", {"city": "Napa"}),
#         ("name with a typo", {"name": "hospise"}),
#         ("mid-word (infix)", {"name": "ospice"}),
#         ("single box: city", {"q": "napa"}),
#         ("single box: zip", {"q": "94559"}),
#         ("single box: mixed", {"q": "hospice california"}),
#     ]

#     healthy = True
#     for label, params in queries:
#         try:
#             result = await svc.search_facilities(page=1, page_size=3, **params)
#             found = result["total"]
#             first = result["items"][0]["name"][:38] if result["items"] else "-"
#             (ok if found else warn)(f"{label:<24} found={found:<8,} e.g. {first}")
#             if not found:
#                 healthy = False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label}: {type(exc).__name__}: {exc}")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # Driver
# # --------------------------------------------------------------------------


# async def run(skip_db: bool) -> int:
#     print(f"\n{B}TypeSense integration doctor{X}")
#     print("=" * 62)

#     # Each gate stops the run: continuing would test the wrong code, or bury
#     # the real cause under cascading failures.
#     if not check_dependency():
#         return 2
#     if not check_modules():
#         print(f"\n{R}Fix the modules above first — every later check would be "
#               f"testing code that is not actually running.{X}\n")
#         return 1
#     if not check_env():
#         print(f"\n{R}Fix the environment above first.{X}\n")
#         return 1
#     if not check_keys():
#         print(f"\n{R}Fix the API keys above first.{X}\n")
#         return 1

#     if skip_db:
#         warn("--skip-db: source table and mapper checks skipped")
#     else:
#         await check_source_table()
#         await check_mapper()

#     _, document_count = await check_collection()
#     check_api_wiring()
#     await check_live_search(document_count)

#     print("\n" + "=" * 62)
#     if _problems:
#         print(f"{R}{B}{len(_problems)} problem(s):{X}\n")
#         for index, problem in enumerate(_problems, 1):
#             print(f"  {index}. {problem}")
#         print()
#         return 1

#     print(f"{G}{B}Everything checks out.{X}\n")
#     if document_count == 0:
#         print("  Next:  python -m scripts.typesense_import --full --dry-run")
#         print("  Then:  python -m scripts.typesense_import --full\n")
#     else:
#         print(f"  Index holds {document_count:,} documents and search is working.")
#         print("  Keep it current with:")
#         print("    python -m scripts.typesense_import --incremental   (every 15 min)")
#         print("    python -m scripts.typesense_import --reconcile     (nightly)\n")
#     return 0


# def main() -> int:
#     parser = argparse.ArgumentParser(description="Check the whole TypeSense integration.")
#     parser.add_argument(
#         "--skip-db",
#         action="store_true",
#         help="skip the Postgres source-table and mapper checks",
#     )
#     args = parser.parse_args()

#     try:
#         return asyncio.run(run(args.skip_db))
#     except KeyboardInterrupt:
#         print("\n  Interrupted.\n")
#         return 2


# if __name__ == "__main__":
#     sys.exit(main())














# """
# TypeSense integration doctor — the single script that checks everything.

#     python scripts/typesense_doctor.py

# Runs 9 checks in dependency order and prints exactly what to do next. The order
# matters: a failure early on makes everything after it meaningless, so the
# script stops rather than burying the real problem under a wall of noise.

# READ-ONLY. Creates nothing, imports nothing, deletes nothing. Safe to run
# against production at any time.

# Exit codes: 0 ready · 1 something is broken · 2 could not run.
# """
# from __future__ import annotations

# import argparse
# import asyncio
# import sys
# from collections import Counter
# from pathlib import Path
# from typing import Any

# # Make `import app...` work regardless of how this file is invoked.
# #
# # `python -m scripts.typesense_doctor` puts the project root on sys.path, but
# # `python scripts/typesense_doctor.py` puts `scripts/` there instead — and then
# # every `from app.core...` fails with ModuleNotFoundError. A diagnostic tool
# # should not be sensitive to how it was started, so the root is added here.
# _PROJECT_ROOT = Path(__file__).resolve().parent.parent
# if str(_PROJECT_ROOT) not in sys.path:
#     sys.path.insert(0, str(_PROJECT_ROOT))

# # --------------------------------------------------------------------------
# # Output
# # --------------------------------------------------------------------------

# _COLOR = sys.stdout.isatty()
# G = "\033[32m" if _COLOR else ""
# R = "\033[31m" if _COLOR else ""
# Y = "\033[33m" if _COLOR else ""
# B = "\033[1m" if _COLOR else ""
# X = "\033[0m" if _COLOR else ""

# _problems: list[str] = []


# def section(title: str) -> None:
#     print(f"\n{B}{title}{X}")
#     print("-" * 62)


# def ok(msg: str) -> None:
#     print(f"  {G}PASS{X}  {msg}")


# def fail(msg: str, fix: str = "") -> None:
#     print(f"  {R}FAIL{X}  {msg}")
#     if fix:
#         print(f"        {Y}fix:{X} {fix}")
#         _problems.append(f"{msg}\n         fix: {fix}")
#     else:
#         _problems.append(msg)


# def warn(msg: str) -> None:
#     print(f"  {Y}WARN{X}  {msg}")


# def info(msg: str) -> None:
#     print(f"        {msg}")


# # --------------------------------------------------------------------------
# # 1. Dependency
# # --------------------------------------------------------------------------

# EXPECTED_TYPESENSE_VERSION = "1.1.1"


# def check_dependency() -> bool:
#     section("1. Dependency")
#     try:
#         import typesense  # noqa: F401
#     except ImportError:
#         fail(
#             "the `typesense` package is not installed",
#             f"pip install typesense=={EXPECTED_TYPESENSE_VERSION}",
#         )
#         return False

#     try:
#         from importlib.metadata import version as pkg_version

#         installed = pkg_version("typesense")
#     except Exception:  # noqa: BLE001
#         installed = "unknown"

#     if installed != EXPECTED_TYPESENSE_VERSION:
#         warn(f"typesense {installed} installed; this code was verified against {EXPECTED_TYPESENSE_VERSION}")
#     else:
#         ok(f"typesense {installed}")
#     return True


# # --------------------------------------------------------------------------
# # 2. Module integrity (functional, not cosmetic)
# # --------------------------------------------------------------------------


# def check_modules() -> bool:
#     """
#     Verify the code that ACTUALLY RUNS, not what the files look like.

#     This deliberately does not compare line counts or diff against a reference
#     copy: comments get edited, files get reformatted, and a cosmetic mismatch
#     is not a defect. What matters is whether the objects Python ends up with
#     behave correctly.

#     That also happens to catch the stale/duplicated-file problem for free. If
#     an older revision is still present — appended below, or commented out and
#     partially restored — Python resolves the LAST definition, so importing the
#     symbol and exercising it reports on the copy that is really in effect.
#     """
#     section("2. Module integrity")
#     healthy = True

#     def probe(label: str, fn) -> bool:
#         nonlocal healthy
#         try:
#             detail = fn()
#         except ModuleNotFoundError as exc:
#             missing = (exc.name or "").split(".")[0]
#             if missing in {"app", "scripts"}:
#                 fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
#             else:
#                 # A third-party package is absent. Saying "stale file" here
#                 # would send the operator editing perfectly good code.
#                 fail(f"{label} — required package {missing!r} is not installed",
#                      f"pip install {missing}   (or: pip install -r requirements.txt)")
#             healthy = False
#             return False
#         except ImportError as exc:
#             fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
#             healthy = False
#             return False
#         except AssertionError as exc:
#             fail(f"{label} — {exc}", "an older revision of this file is what is actually running")
#             healthy = False
#             return False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label} — {type(exc).__name__}: {exc}")
#             healthy = False
#             return False
#         ok(f"{label}{f' — {detail}' if detail else ''}")
#         return True

#     # ---- config ----
#     def _config():
#         from app.core.config import settings

#         for attr in ("TYPESENSE_ADMIN_API_KEY", "TYPESENSE_COLLECTION", "TYPESENSE_SEARCH_ENABLED"):
#             assert hasattr(settings, attr), f"settings is missing {attr}"
#         assert hasattr(settings, "typesense_nodes"), "settings is missing the typesense_nodes property"
#         assert isinstance(settings.typesense_nodes, list), "typesense_nodes must return a list"
#         return "settings expose the TypeSense config"

#     probe("app.core.config", _config)

#     # ---- client ----
#     def _client():
#         from app.core import typesense as mod

#         for name in (
#             "use_admin_credentials",
#             "get_typesense_client",
#             "run_typesense",
#             "check_typesense_connection",
#             "is_configured",
#             "TypesenseUnavailable",
#         ):
#             assert hasattr(mod, name), f"`{name}` is missing"
#         # Confirm the health check makes an AUTHENTICATED call. Checked via the
#         # AST rather than a substring, because the current docstring mentions
#         # `operations.is_healthy` precisely to explain why it is NOT used — a
#         # plain `in source` test matches that prose and reports a false failure.
#         #
#         # Collects every attribute EXPRESSION, not just direct calls: these
#         # operations are handed to `run_typesense(client.collections.retrieve)`
#         # as references, so the attribute is an argument rather than the
#         # callee, and a call-only walk misses it entirely.
#         import ast as _ast
#         import inspect
#         import textwrap

#         tree = _ast.parse(textwrap.dedent(inspect.getsource(mod.check_typesense_connection)))
#         referenced = {
#             _ast.unparse(node)
#             for node in _ast.walk(tree)
#             if isinstance(node, _ast.Attribute)
#         }
#         assert not any(r.endswith("operations.is_healthy") for r in referenced), (
#             "the health check uses operations.is_healthy, which is UNAUTHENTICATED "
#             "and returns true even for a wrong key"
#         )
#         assert any("collections" in r for r in referenced), (
#             "the health check makes no authenticated call"
#         )
#         return "admin-credential switch + authenticated health check present"

#     probe("app.core.typesense", _client)

#     # ---- collection schema ----
#     def _collection():
#         from app.services.typesense_collection_service import (
#             FIELD_NUM_TYPOS,
#             INFIX_FIELDS,
#             QUERY_BY_FIELDS,
#             QUERY_BY_WEIGHTS,
#             build_schema,
#         )

#         schema = build_schema()
#         fields = {f["name"] for f in schema["fields"]}

#         assert schema["default_sorting_field"] == "rating_sort", "default_sorting_field is wrong"
#         assert "rating_sort" in fields, "rating_sort field missing"
#         assert not any(
#             f["name"] == "rating_sort" and f.get("optional") for f in schema["fields"]
#         ), "rating_sort must NOT be optional — TypeSense rejects the collection otherwise"
#         assert "zip_code" in QUERY_BY_FIELDS, "zip_code is not searchable"
#         assert len(QUERY_BY_WEIGHTS) == len(QUERY_BY_FIELDS), "weights/fields arity mismatch"
#         assert FIELD_NUM_TYPOS.get("state") == 0, "state must not tolerate typos (CA vs GA)"
#         assert FIELD_NUM_TYPOS.get("zip_code") == 0, "zip_code must not tolerate typos"
#         declared_infix = {f["name"] for f in schema["fields"] if f.get("infix")}
#         assert INFIX_FIELDS == declared_infix, "INFIX_FIELDS disagrees with the schema"
#         assert set(QUERY_BY_FIELDS) <= fields, "a searched field is not in the schema"
#         return f"{len(fields)} fields, searchable: {', '.join(QUERY_BY_FIELDS)}"

#     probe("app.services.typesense_collection_service", _collection)

#     # ---- mapper ----
#     def _mapper():
#         from app.utils.facility_mapper import build_select_sql, to_document

#         doc = to_document(
#             {
#                 "uuid": "abc",
#                 "name": "TEST FACILITY",
#                 "city": "NAPA",
#                 "state": " ca ",
#                 "ownership_type": "unknown",
#                 "latitude": "38.3",
#                 "longitude": "",
#                 "bed_count": "1,200",
#                 "updated_at": "2026-07-22 09:39:30",
#             }
#         )
#         assert doc["state"] == "CA", "state is not upper-cased"
#         assert "ownership_type" not in doc, "'unknown' was not normalised away"
#         assert "latitude" not in doc, "a lone coordinate was kept"
#         assert doc["bed_count"] == 1200, "thousands separator not handled"
#         assert doc["rating_sort"] == 0.0, "rating_sort not always emitted"
#         assert None not in doc.values(), "explicit None emitted — TypeSense rejects those"

#         sql = build_select_sql()
#         assert "OFFSET" not in sql.upper(), "still using OFFSET pagination"
#         assert ":after_uuid" in sql and ":since" in sql, "keyset/incremental params missing"
#         assert 'public."All_State_Type_combined"' in sql, "source table not quoted correctly"
#         return "coercion, null handling and keyset SQL all correct"

#     probe("app.utils.facility_mapper", _mapper)

#     # ---- search service ----
#     def _search():
#         from app.services.typesense_search_service import build_search_params

#         params = build_search_params(
#             q="94559", name=None, city=None, state="CA", zip_code=None,
#             facility_type=None, facility_type_category=None, page=1, page_size=20,
#         )
#         arities = {len(params[k].split(",")) for k in ("query_by", "query_by_weights", "num_typos", "infix")}
#         assert len(arities) == 1, f"per-field parameter arity mismatch: {arities}"
#         assert params["num_typos"].endswith("0"), "zip_code typo tolerance is not 0"
#         assert "filter_by" in params and "state:=" in params["filter_by"], "state filter not applied"
#         assert None not in params.values(), "None value would be rejected by TypeSense"
#         return f"query_by={params['query_by']} num_typos={params['num_typos']}"

#     probe("app.services.typesense_search_service", _search)

#     # ---- sync service ----
#     def _sync():
#         from app.services import typesense_sync_service as mod

#         for name in (
#             "run_full_sync",
#             "run_incremental_sync",
#             "reconcile_deletions",
#             "verify_sync",
#             "get_index_watermark",
#         ):
#             assert hasattr(mod, name), f"`{name}` is missing"
#         return "full / incremental / reconcile / verify all present"

#     probe("app.services.typesense_sync_service", _sync)

#     # ---- import CLI ----
#     def _cli():
#         from scripts import typesense_import as mod

#         assert hasattr(mod, "main"), "`main` is missing"
#         return None

#     probe("scripts.typesense_import", _cli)

#     return healthy


# # --------------------------------------------------------------------------
# # 3. Environment
# # --------------------------------------------------------------------------


# def check_env() -> bool:
#     section("3. Environment")
#     try:
#         from app.core.config import settings
#     except ModuleNotFoundError as exc:
#         # Not an environment problem at all — the project root is not on
#         # sys.path, or a package is missing. Saying "check .env" here would
#         # send the operator hunting in entirely the wrong file.
#         fail(
#             f"could not import the app package — {exc}",
#             f"run from the project root ({_PROJECT_ROOT}), "
#             "and make sure the virtualenv is active",
#         )
#         return False
#     except Exception as exc:  # noqa: BLE001
#         fail(
#             f"could not load settings — {type(exc).__name__}: {exc}",
#             "check .env for lines python-dotenv could not parse, "
#             "and for a required variable that is missing",
#         )
#         return False

#     healthy = True

#     nodes = settings.typesense_nodes
#     if not nodes:
#         fail("no TypeSense nodes configured", "set TYPESENSE_HOST (or TYPESENSE_NODES) in .env")
#         healthy = False
#     else:
#         rendered = ", ".join(f"{n['protocol']}://{n['host']}:{n['port']}" for n in nodes)
#         ok(f"nodes: {rendered}")

#     def inspect_key(label: str, value: str, required: bool) -> bool:
#         if not value:
#             if required:
#                 fail(f"{label} is EMPTY", "set it in .env — check the dotenv parse warnings")
#                 return False
#             warn(f"{label} not set — the import will fall back to the search key and likely 401")
#             return True
#         if value != value.strip():
#             fail(f"{label} has surrounding whitespace", "remove spaces/newlines around the value")
#             return False
#         if value[0] in "\"'" or value[-1] in "\"'":
#             fail(f"{label} has stray quotes", "write it unquoted in .env")
#             return False
#         ok(f"{label}: set ({len(value)} chars, ends {value[-4:]!r})")
#         return True

#     if not inspect_key("TYPESENSE_API_KEY (search)", settings.TYPESENSE_API_KEY, True):
#         healthy = False
#     if not inspect_key("TYPESENSE_ADMIN_API_KEY", settings.TYPESENSE_ADMIN_API_KEY, False):
#         healthy = False

#     if settings.TYPESENSE_API_KEY and settings.TYPESENSE_API_KEY == settings.TYPESENSE_ADMIN_API_KEY:
#         warn("both keys are identical — the running app should use a search-only key")

#     ok(f"collection: {settings.TYPESENSE_COLLECTION}")
#     if not settings.TYPESENSE_SEARCH_ENABLED:
#         warn("TYPESENSE_SEARCH_ENABLED=false — the app will serve search from Postgres")

#     return healthy


# # --------------------------------------------------------------------------
# # 4. API keys
# # --------------------------------------------------------------------------


# def check_keys() -> bool:
#     """
#     Authenticate each key separately and report its real capability.

#     Uses an authenticated call, deliberately NOT `operations.is_healthy`: that
#     maps to TypeSense's `GET /health`, which is UNAUTHENTICATED and returns
#     true for a wrong, revoked or empty key. A check built on it reports
#     success, then the next call 401s — pointing the operator at the network
#     when the problem is the key.
#     """
#     section("4. API keys")
#     import typesense
#     from typesense.exceptions import ObjectNotFound, RequestForbidden, RequestUnauthorized

#     from app.core.config import settings

#     def build(api_key: str) -> typesense.Client:
#         return typesense.Client(
#             {
#                 "nodes": settings.typesense_nodes,
#                 "api_key": api_key,
#                 "connection_timeout_seconds": 8.0,
#                 "num_retries": 1,
#             }
#         )

#     healthy = True
#     admin_key = settings.TYPESENSE_ADMIN_API_KEY or settings.TYPESENSE_API_KEY

#     for label, api_key, needs_admin in (
#         ("ADMIN key ", admin_key, True),
#         ("SEARCH key", settings.TYPESENSE_API_KEY, False),
#     ):
#         if not api_key:
#             warn(f"{label}: not set, skipped")
#             continue

#         client = build(api_key)

#         try:
#             collections = client.collections.retrieve()
#             names = [c["name"] for c in collections]
#             ok(f"{label}: ADMIN access — collections: {names or '(none yet)'}")
#             continue
#         except (RequestUnauthorized, RequestForbidden):
#             if needs_admin:
#                 fail(
#                     f"{label}: 401/403 — this key has no admin rights",
#                     "put the Admin API Key (not the Search Only key) in TYPESENSE_ADMIN_API_KEY",
#                 )
#                 healthy = False
#                 continue
#         except Exception as exc:  # noqa: BLE001
#             fail(
#                 f"{label}: cannot reach TypeSense — {type(exc).__name__}: {exc}",
#                 "check the host/port in .env and that the cluster is running",
#             )
#             healthy = False
#             continue

#         try:
#             client.collections[settings.TYPESENSE_COLLECTION].documents.search(
#                 {"q": "*", "query_by": "name", "per_page": 1}
#             )
#             ok(f"{label}: SEARCH access on {settings.TYPESENSE_COLLECTION!r}")
#         except ObjectNotFound:
#             ok(f"{label}: authenticated (collection not created yet — expected before first import)")
#         except (RequestUnauthorized, RequestForbidden):
#             fail(
#                 f"{label}: 401/403 — invalid, revoked, or scoped to another collection",
#                 "regenerate the key in the TypeSense Cloud dashboard",
#             )
#             healthy = False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label}: {type(exc).__name__}: {exc}")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # 5. Source table
# # --------------------------------------------------------------------------


# async def check_source_table() -> bool:
#     section("5. Postgres source table")
#     from sqlalchemy import text

#     from app.core.database import AsyncSessionLocal
#     from app.utils.facility_mapper import SOURCE_COLUMNS, SOURCE_TABLE

#     try:
#         async with AsyncSessionLocal() as session:
#             result = await session.execute(
#                 text(
#                     "SELECT column_name FROM information_schema.columns "
#                     "WHERE table_schema = 'public' AND table_name = 'All_State_Type_combined'"
#                 )
#             )
#             available = {row[0] for row in result.all()}

#             if not available:
#                 fail(
#                     f"{SOURCE_TABLE} not found or not readable",
#                     "check the table name, schema, and database credentials",
#                 )
#                 return False
#             ok(f"{SOURCE_TABLE} found ({len(available)} columns)")

#             missing = [col for col in SOURCE_COLUMNS if col not in available]
#             if missing:
#                 fail(
#                     f"columns the mapper needs are missing: {', '.join(missing)}",
#                     "the source schema changed — update SOURCE_COLUMNS in facility_mapper.py",
#                 )
#                 return False
#             ok(f"all {len(SOURCE_COLUMNS)} mapped columns present")

#             count = (
#                 await session.execute(text('SELECT COUNT(*) FROM public."All_State_Type_combined"'))
#             ).scalar_one()
#             ok(f"{count:,} rows")

#             nulls = (
#                 await session.execute(
#                     text(
#                         'SELECT COUNT(*) FROM public."All_State_Type_combined" '
#                         'WHERE "updated_at" IS NULL'
#                     )
#                 )
#             ).scalar_one()
#             if nulls:
#                 warn(f"{nulls:,} rows have NULL updated_at — incremental sync will always skip them")
#             else:
#                 ok("updated_at populated on every row — incremental sync will work")
#             return True
#     except Exception as exc:  # noqa: BLE001
#         fail(
#             f"cannot query Postgres — {type(exc).__name__}: {exc}",
#             "check DATABASE_URL / ASYNC_DATABASE_URL",
#         )
#         return False


# # --------------------------------------------------------------------------
# # 6. Mapper
# # --------------------------------------------------------------------------


# async def check_mapper() -> bool:
#     """Map REAL rows, so the check reflects the actual data rather than a fixture."""
#     section("6. Mapper (against real rows)")
#     from sqlalchemy import text

#     from app.core.database import AsyncSessionLocal
#     from app.utils.facility_mapper import build_select_sql, to_documents

#     try:
#         async with AsyncSessionLocal() as session:
#             result = await session.execute(
#                 text(build_select_sql()), {"after_uuid": None, "since": None, "limit": 200}
#             )
#             rows = [dict(row) for row in result.mappings()]
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not read sample rows — {type(exc).__name__}: {exc}")
#         return False

#     if not rows:
#         fail("the source table returned no rows")
#         return False

#     documents, errors = to_documents(rows)
#     ok(f"mapped {len(documents)}/{len(rows)} sample rows")

#     if errors:
#         warn(f"{len(errors)} rows unmappable (no uuid or no name) — they will not be searchable")
#         for message in errors[:3]:
#             info(message)

#     if not documents:
#         fail("no rows produced a valid document")
#         return False

#     coverage: Counter[str] = Counter()
#     for doc in documents:
#         coverage.update(doc.keys())
#     total = len(documents)

#     info("field coverage across the sample:")
#     for field in (
#         "name", "city", "state", "zip_code", "address",
#         "facility_type_category", "ownership_type",
#         "latitude", "overall_rating", "bed_count",
#     ):
#         pct = coverage.get(field, 0) / total * 100
#         info(f"  {field:<24} {pct:5.1f}%  {'#' * int(pct / 5)}")

#     sample = documents[0]
#     info(f"sample: {sample.get('name')!r} — {sample.get('city')}, {sample.get('state')}")
#     return True


# # --------------------------------------------------------------------------
# # 7. Collection
# # --------------------------------------------------------------------------


# async def check_collection() -> tuple[bool, int]:
#     section("7. TypeSense collection")
#     from app.core.config import settings
#     from app.core.typesense import get_typesense_client, run_typesense, use_admin_credentials
#     from app.services.typesense_collection_service import build_schema, get_collection_stats

#     # This process only reads, but reading the collection schema is itself an
#     # admin operation.
#     use_admin_credentials()

#     try:
#         stats = await get_collection_stats()
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not read the collection — {type(exc).__name__}: {exc}")
#         return False, 0

#     if stats is None:
#         warn(f"collection {settings.TYPESENSE_COLLECTION!r} does not exist yet")
#         info("expected before the first import")
#         return True, 0

#     ok(f"collection {stats['name']!r} exists")
#     ok(f"{stats['num_documents']:,} documents indexed")

#     expected_fields = {f["name"] for f in build_schema()["fields"]}
#     client = get_typesense_client()
#     live = await run_typesense(client.collections[settings.TYPESENSE_COLLECTION].retrieve)
#     live_fields = {f["name"] for f in live.get("fields", [])}

#     missing = expected_fields - live_fields
#     if missing:
#         fail(
#             f"the live collection is missing fields: {', '.join(sorted(missing))}",
#             "schema changed — run: python -m scripts.typesense_import --full --recreate",
#         )
#         return False, stats["num_documents"]

#     ok(f"schema matches ({len(expected_fields)} fields)")
#     return True, stats["num_documents"]


# # --------------------------------------------------------------------------
# # 8. API wiring
# # --------------------------------------------------------------------------


# def check_api_wiring() -> bool:
#     """
#     Verify the endpoint file is wired correctly.

#     Two failure modes here are silent rather than loud:

#     * A duplicate route — FastAPI keeps the FIRST registration, so a leftover
#       old handler wins, the TypeSense path is never called, and nothing errors.
#     * "/{facility_id}" declared before "/search" — the catch-all swallows the
#       literal paths and every search 400s with "Invalid facility id".
#     """
#     section("8. API wiring")
#     try:
#         from app.api.v1.endpoints.facilities import router
#     except Exception as exc:  # noqa: BLE001
#         fail(f"could not import the facilities endpoint — {type(exc).__name__}: {exc}")
#         return False

#     routes = [(r.path, tuple(sorted(r.methods))) for r in router.routes]
#     healthy = True

#     # The router is built with prefix="/facilities", and FastAPI applies that
#     # when the route is added — so `route.path` is "/facilities/search", not
#     # "/search". Comparing against bare paths reports every route as missing,
#     # and worse, makes the catch-all ordering check pass vacuously because
#     # "/{facility_id}" is not in the list either.
#     prefix = getattr(router, "prefix", "") or ""
#     paths = [path for path, _ in routes]

#     def registered(path: str) -> bool:
#         return f"{prefix}{path}" in paths or path in paths

#     duplicates = [path for path, count in Counter(routes).items() if count > 1]
#     if duplicates:
#         fail(
#             f"duplicate routes registered: {duplicates}",
#             "an old copy of a handler is still in facilities.py — delete it",
#         )
#         healthy = False
#     else:
#         ok(f"{len(routes)} routes, no duplicates{f' (prefix {prefix!r})' if prefix else ''}")

#     catch_all = f"{prefix}/{{facility_id}}"
#     if catch_all not in paths:
#         fail(f"route {catch_all} is not registered")
#         healthy = False
#     elif paths[-1] != catch_all:
#         fail(
#             f"'{catch_all}' is not declared last — it will swallow /search and /suggest",
#             "move get_facility_detail to the bottom of facilities.py",
#         )
#         healthy = False
#     else:
#         ok(f"catch-all '{catch_all}' is declared last")

#     for required in ("/search", "/suggest", "/recommended"):
#         if registered(required):
#             ok(f"route {prefix}{required} registered")
#         else:
#             fail(f"route {prefix}{required} is not registered")
#             healthy = False

#     import inspect

#     from app.api.v1.endpoints import facilities as module

#     source = inspect.getsource(module)
#     for marker, label in (
#         ("typesense_search_service", "TypeSense wired into the endpoint"),
#         ("_search_facilities_postgres", "Postgres fallback present"),
#         ("TypesenseUnavailable", "fallback exception handling present"),
#     ):
#         if marker in source:
#             ok(label)
#         else:
#             fail(f"{label} — NOT found in facilities.py", "replace it with the final version")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # 9. Live search
# # --------------------------------------------------------------------------


# async def check_live_search(document_count: int) -> bool:
#     section("9. Live search")
#     if document_count == 0:
#         warn("index is empty — skipping (run the import first)")
#         return True

#     from app.services import typesense_search_service as svc

#     queries: list[tuple[str, dict[str, Any]]] = [
#         ("filters only (browse)", {"state": "CA"}),
#         ("city", {"city": "Napa"}),
#         ("name with a typo", {"name": "hospise"}),
#         ("mid-word (infix)", {"name": "ospice"}),
#         ("single box: city", {"q": "napa"}),
#         ("single box: zip", {"q": "94559"}),
#         ("single box: mixed", {"q": "hospice california"}),
#     ]

#     healthy = True
#     for label, params in queries:
#         try:
#             result = await svc.search_facilities(page=1, page_size=3, **params)
#             found = result["total"]
#             first = result["items"][0]["name"][:38] if result["items"] else "-"
#             (ok if found else warn)(f"{label:<24} found={found:<8,} e.g. {first}")
#             if not found:
#                 healthy = False
#         except Exception as exc:  # noqa: BLE001
#             fail(f"{label}: {type(exc).__name__}: {exc}")
#             healthy = False

#     return healthy


# # --------------------------------------------------------------------------
# # Driver
# # --------------------------------------------------------------------------


# async def run(skip_db: bool) -> int:
#     print(f"\n{B}TypeSense integration doctor{X}")
#     print("=" * 62)

#     # Each gate stops the run: continuing would test the wrong code, or bury
#     # the real cause under cascading failures.
#     if not check_dependency():
#         return 2
#     if not check_modules():
#         print(f"\n{R}Fix the modules above first — every later check would be "
#               f"testing code that is not actually running.{X}\n")
#         return 1
#     if not check_env():
#         print(f"\n{R}Fix the environment above first.{X}\n")
#         return 1
#     if not check_keys():
#         print(f"\n{R}Fix the API keys above first.{X}\n")
#         return 1

#     if skip_db:
#         warn("--skip-db: source table and mapper checks skipped")
#     else:
#         await check_source_table()
#         await check_mapper()

#     _, document_count = await check_collection()
#     check_api_wiring()
#     await check_live_search(document_count)

#     print("\n" + "=" * 62)
#     if _problems:
#         print(f"{R}{B}{len(_problems)} problem(s):{X}\n")
#         for index, problem in enumerate(_problems, 1):
#             print(f"  {index}. {problem}")
#         print()
#         return 1

#     print(f"{G}{B}Everything checks out.{X}\n")
#     if document_count == 0:
#         print("  Next:  python -m scripts.typesense_import --full --dry-run")
#         print("  Then:  python -m scripts.typesense_import --full\n")
#     else:
#         print(f"  Index holds {document_count:,} documents and search is working.")
#         print("  Keep it current with:")
#         print("    python -m scripts.typesense_import --incremental   (every 15 min)")
#         print("    python -m scripts.typesense_import --reconcile     (nightly)\n")
#     return 0


# def main() -> int:
#     parser = argparse.ArgumentParser(description="Check the whole TypeSense integration.")
#     parser.add_argument(
#         "--skip-db",
#         action="store_true",
#         help="skip the Postgres source-table and mapper checks",
#     )
#     args = parser.parse_args()

#     try:
#         return asyncio.run(run(args.skip_db))
#     except KeyboardInterrupt:
#         print("\n  Interrupted.\n")
#         return 2


# if __name__ == "__main__":
#     sys.exit(main())















#!/usr/bin/env python
"""
TypeSense integration doctor — the single script that checks everything.

    python scripts/typesense_doctor.py

Runs 9 checks in dependency order and prints exactly what to do next. The order
matters: a failure early on makes everything after it meaningless, so the
script stops rather than burying the real problem under a wall of noise.

READ-ONLY. Creates nothing, imports nothing, deletes nothing. Safe to run
against production at any time.

Exit codes: 0 ready · 1 something is broken · 2 could not run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Make `import app...` work regardless of how this file is invoked.
#
# `python -m scripts.typesense_doctor` puts the project root on sys.path, but
# `python scripts/typesense_doctor.py` puts `scripts/` there instead — and then
# every `from app.core...` fails with ModuleNotFoundError. A diagnostic tool
# should not be sensitive to how it was started, so the root is added here.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

_COLOR = sys.stdout.isatty()
G = "\033[32m" if _COLOR else ""
R = "\033[31m" if _COLOR else ""
Y = "\033[33m" if _COLOR else ""
B = "\033[1m" if _COLOR else ""
X = "\033[0m" if _COLOR else ""

_problems: list[str] = []


def section(title: str) -> None:
    print(f"\n{B}{title}{X}")
    print("-" * 62)


def ok(msg: str) -> None:
    print(f"  {G}PASS{X}  {msg}")


def fail(msg: str, fix: str = "") -> None:
    print(f"  {R}FAIL{X}  {msg}")
    if fix:
        print(f"        {Y}fix:{X} {fix}")
        _problems.append(f"{msg}\n         fix: {fix}")
    else:
        _problems.append(msg)


def warn(msg: str) -> None:
    print(f"  {Y}WARN{X}  {msg}")


def info(msg: str) -> None:
    print(f"        {msg}")


# --------------------------------------------------------------------------
# 1. Dependency
# --------------------------------------------------------------------------

EXPECTED_TYPESENSE_VERSION = "1.1.1"


def check_dependency() -> bool:
    section("1. Dependency")
    try:
        import typesense  # noqa: F401
    except ImportError:
        fail(
            "the `typesense` package is not installed",
            f"pip install typesense=={EXPECTED_TYPESENSE_VERSION}",
        )
        return False

    try:
        from importlib.metadata import version as pkg_version

        installed = pkg_version("typesense")
    except Exception:  # noqa: BLE001
        installed = "unknown"

    if installed != EXPECTED_TYPESENSE_VERSION:
        warn(f"typesense {installed} installed; this code was verified against {EXPECTED_TYPESENSE_VERSION}")
    else:
        ok(f"typesense {installed}")
    return True


# --------------------------------------------------------------------------
# 2. Module integrity (functional, not cosmetic)
# --------------------------------------------------------------------------


def check_modules() -> bool:
    """
    Verify the code that ACTUALLY RUNS, not what the files look like.

    This deliberately does not compare line counts or diff against a reference
    copy: comments get edited, files get reformatted, and a cosmetic mismatch
    is not a defect. What matters is whether the objects Python ends up with
    behave correctly.

    That also happens to catch the stale/duplicated-file problem for free. If
    an older revision is still present — appended below, or commented out and
    partially restored — Python resolves the LAST definition, so importing the
    symbol and exercising it reports on the copy that is really in effect.
    """
    section("2. Module integrity")
    healthy = True

    def probe(label: str, fn) -> bool:
        nonlocal healthy
        try:
            detail = fn()
        except ModuleNotFoundError as exc:
            missing = (exc.name or "").split(".")[0]
            if missing in {"app", "scripts"}:
                fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
            else:
                # A third-party package is absent. Saying "stale file" here
                # would send the operator editing perfectly good code.
                fail(f"{label} — required package {missing!r} is not installed",
                     f"pip install {missing}   (or: pip install -r requirements.txt)")
            healthy = False
            return False
        except ImportError as exc:
            fail(f"{label} — {exc}", "this file is stale; replace it with the final version")
            healthy = False
            return False
        except AssertionError as exc:
            fail(f"{label} — {exc}", "an older revision of this file is what is actually running")
            healthy = False
            return False
        except Exception as exc:  # noqa: BLE001
            fail(f"{label} — {type(exc).__name__}: {exc}")
            healthy = False
            return False
        ok(f"{label}{f' — {detail}' if detail else ''}")
        return True

    # ---- config ----
    def _config():
        from app.core.config import settings

        for attr in ("TYPESENSE_ADMIN_API_KEY", "TYPESENSE_COLLECTION", "TYPESENSE_SEARCH_ENABLED"):
            assert hasattr(settings, attr), f"settings is missing {attr}"
        assert hasattr(settings, "typesense_nodes"), "settings is missing the typesense_nodes property"
        assert isinstance(settings.typesense_nodes, list), "typesense_nodes must return a list"
        return "settings expose the TypeSense config"

    probe("app.core.config", _config)

    # ---- client ----
    def _client():
        from app.core import typesense as mod

        for name in (
            "use_admin_credentials",
            "get_typesense_client",
            "run_typesense",
            "check_typesense_connection",
            "is_configured",
            "TypesenseUnavailable",
        ):
            assert hasattr(mod, name), f"`{name}` is missing"
        return "client, admin-credential switch and error type all present"

    probe("app.core.typesense", _client)

    # ---- collection schema ----
    def _collection():
        from app.services.typesense_collection_service import (
            FIELD_NUM_TYPOS,
            INFIX_FIELDS,
            QUERY_BY_FIELDS,
            QUERY_BY_WEIGHTS,
            build_schema,
        )

        schema = build_schema()
        fields = {f["name"] for f in schema["fields"]}

        assert schema["default_sorting_field"] == "rating_sort", "default_sorting_field is wrong"
        assert "rating_sort" in fields, "rating_sort field missing"
        assert not any(
            f["name"] == "rating_sort" and f.get("optional") for f in schema["fields"]
        ), "rating_sort must NOT be optional — TypeSense rejects the collection otherwise"
        assert "zip_code" in QUERY_BY_FIELDS, "zip_code is not searchable"
        assert len(QUERY_BY_WEIGHTS) == len(QUERY_BY_FIELDS), "weights/fields arity mismatch"
        assert FIELD_NUM_TYPOS.get("state") == 0, "state must not tolerate typos (CA vs GA)"
        assert FIELD_NUM_TYPOS.get("zip_code") == 0, "zip_code must not tolerate typos"
        declared_infix = {f["name"] for f in schema["fields"] if f.get("infix")}
        assert INFIX_FIELDS == declared_infix, "INFIX_FIELDS disagrees with the schema"
        assert set(QUERY_BY_FIELDS) <= fields, "a searched field is not in the schema"
        return f"{len(fields)} fields, searchable: {', '.join(QUERY_BY_FIELDS)}"

    probe("app.services.typesense_collection_service", _collection)

    # ---- mapper ----
    def _mapper():
        from app.utils.facility_mapper import build_select_sql, to_document

        doc = to_document(
            {
                "uuid": "abc",
                "name": "TEST FACILITY",
                "city": "NAPA",
                "state": " ca ",
                "ownership_type": "unknown",
                "latitude": "38.3",
                "longitude": "",
                "bed_count": "1,200",
                "updated_at": "2026-07-22 09:39:30",
            }
        )
        assert doc["state"] == "CA", "state is not upper-cased"
        assert "ownership_type" not in doc, "'unknown' was not normalised away"
        assert "latitude" not in doc, "a lone coordinate was kept"
        assert doc["bed_count"] == 1200, "thousands separator not handled"
        assert doc["rating_sort"] == 0.0, "rating_sort not always emitted"
        assert None not in doc.values(), "explicit None emitted — TypeSense rejects those"

        sql = build_select_sql()
        assert "OFFSET" not in sql.upper(), "still using OFFSET pagination"
        assert ":after_uuid" in sql and ":since" in sql, "keyset/incremental params missing"
        assert 'public."Final Table"' in sql, "source table not quoted correctly"
        return "coercion, null handling and keyset SQL all correct"

    probe("app.utils.facility_mapper", _mapper)

    # ---- search service ----
    def _search():
        from app.services.typesense_search_service import build_search_params

        params = build_search_params(
            q="94559", name=None, city=None, state="CA", zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        arities = {len(params[k].split(",")) for k in ("query_by", "query_by_weights", "num_typos", "infix")}
        assert len(arities) == 1, f"per-field parameter arity mismatch: {arities}"
        assert params["num_typos"].endswith("0"), "zip_code typo tolerance is not 0"
        assert "filter_by" in params and "state:=" in params["filter_by"], "state filter not applied"
        assert None not in params.values(), "None value would be rejected by TypeSense"
        return f"query_by={params['query_by']} num_typos={params['num_typos']}"

    probe("app.services.typesense_search_service", _search)

    # ---- sync service ----
    def _sync():
        from app.services import typesense_sync_service as mod

        for name in (
            "run_full_sync",
            "run_incremental_sync",
            "reconcile_deletions",
            "verify_sync",
            "get_index_watermark",
        ):
            assert hasattr(mod, name), f"`{name}` is missing"
        return "full / incremental / reconcile / verify all present"

    probe("app.services.typesense_sync_service", _sync)

    # ---- import CLI ----
    def _cli():
        from app.core import typesense as ts_mod
        from scripts import typesense_import as mod

        assert hasattr(mod, "main"), "`main` is missing"

        # The script must switch to the admin key before touching TypeSense —
        # otherwise the import runs on the search-only key and 401s at
        # collection creation. Rather than inspect the code, just call the
        # switch the way the script does and confirm the client then uses the
        # admin key. This tests behaviour, not text.
        assert hasattr(mod, "use_admin_credentials"), (
            "the import script does not import `use_admin_credentials` — this is the "
            "OLD version and will run on the search-only key (401)"
        )
        from app.core.config import settings

        if settings.TYPESENSE_ADMIN_API_KEY:
            ts_mod.reset_typesense_client()
            mod.use_admin_credentials()
            active = ts_mod._active_api_key()
            ts_mod.reset_typesense_client()  # leave no admin client behind
            assert active == settings.TYPESENSE_ADMIN_API_KEY, (
                "calling the script's admin switch did not activate the admin key"
            )
            return "acquires admin credentials correctly"
        return "present (admin key not set, cannot verify the switch)"

    probe("scripts.typesense_import", _cli)

    return healthy


# --------------------------------------------------------------------------
# 3. Environment
# --------------------------------------------------------------------------


def check_env() -> bool:
    section("3. Environment")
    try:
        from app.core.config import settings
    except ModuleNotFoundError as exc:
        # Not an environment problem at all — the project root is not on
        # sys.path, or a package is missing. Saying "check .env" here would
        # send the operator hunting in entirely the wrong file.
        fail(
            f"could not import the app package — {exc}",
            f"run from the project root ({_PROJECT_ROOT}), "
            "and make sure the virtualenv is active",
        )
        return False
    except Exception as exc:  # noqa: BLE001
        fail(
            f"could not load settings — {type(exc).__name__}: {exc}",
            "check .env for lines python-dotenv could not parse, "
            "and for a required variable that is missing",
        )
        return False

    healthy = True

    nodes = settings.typesense_nodes
    if not nodes:
        fail("no TypeSense nodes configured", "set TYPESENSE_HOST (or TYPESENSE_NODES) in .env")
        healthy = False
    else:
        rendered = ", ".join(f"{n['protocol']}://{n['host']}:{n['port']}" for n in nodes)
        ok(f"nodes: {rendered}")

    def inspect_key(label: str, value: str, required: bool) -> bool:
        if not value:
            if required:
                fail(f"{label} is EMPTY", "set it in .env — check the dotenv parse warnings")
                return False
            warn(f"{label} not set — the import will fall back to the search key and likely 401")
            return True
        if value != value.strip():
            fail(f"{label} has surrounding whitespace", "remove spaces/newlines around the value")
            return False
        if value[0] in "\"'" or value[-1] in "\"'":
            fail(f"{label} has stray quotes", "write it unquoted in .env")
            return False
        ok(f"{label}: set ({len(value)} chars, ends {value[-4:]!r})")
        return True

    if not inspect_key("TYPESENSE_API_KEY (search)", settings.TYPESENSE_API_KEY, True):
        healthy = False
    if not inspect_key("TYPESENSE_ADMIN_API_KEY", settings.TYPESENSE_ADMIN_API_KEY, False):
        healthy = False

    if settings.TYPESENSE_API_KEY and settings.TYPESENSE_API_KEY == settings.TYPESENSE_ADMIN_API_KEY:
        warn("both keys are identical — the running app should use a search-only key")

    ok(f"collection: {settings.TYPESENSE_COLLECTION}")
    if not settings.TYPESENSE_SEARCH_ENABLED:
        warn("TYPESENSE_SEARCH_ENABLED=false — the app will serve search from Postgres")

    return healthy


# --------------------------------------------------------------------------
# 4. API keys
# --------------------------------------------------------------------------


def check_keys() -> bool:
    """
    Authenticate each key separately and report its real capability.

    Uses an authenticated call, deliberately NOT `operations.is_healthy`: that
    maps to TypeSense's `GET /health`, which is UNAUTHENTICATED and returns
    true for a wrong, revoked or empty key. A check built on it reports
    success, then the next call 401s — pointing the operator at the network
    when the problem is the key.
    """
    section("4. API keys")
    import typesense
    from typesense.exceptions import ObjectNotFound, RequestForbidden, RequestUnauthorized

    from app.core.config import settings

    def build(api_key: str) -> typesense.Client:
        return typesense.Client(
            {
                "nodes": settings.typesense_nodes,
                "api_key": api_key,
                "connection_timeout_seconds": 8.0,
                "num_retries": 1,
            }
        )

    healthy = True
    admin_key = settings.TYPESENSE_ADMIN_API_KEY or settings.TYPESENSE_API_KEY

    for label, api_key, needs_admin in (
        ("ADMIN key ", admin_key, True),
        ("SEARCH key", settings.TYPESENSE_API_KEY, False),
    ):
        if not api_key:
            warn(f"{label}: not set, skipped")
            continue

        client = build(api_key)

        try:
            collections = client.collections.retrieve()
            names = [c["name"] for c in collections]
            ok(f"{label}: ADMIN access — collections: {names or '(none yet)'}")
            continue
        except (RequestUnauthorized, RequestForbidden):
            if needs_admin:
                fail(
                    f"{label}: 401/403 — this key has no admin rights",
                    "put the Admin API Key (not the Search Only key) in TYPESENSE_ADMIN_API_KEY",
                )
                healthy = False
                continue
        except Exception as exc:  # noqa: BLE001
            fail(
                f"{label}: cannot reach TypeSense — {type(exc).__name__}: {exc}",
                "check the host/port in .env and that the cluster is running",
            )
            healthy = False
            continue

        try:
            client.collections[settings.TYPESENSE_COLLECTION].documents.search(
                {"q": "*", "query_by": "name", "per_page": 1}
            )
            ok(f"{label}: SEARCH access on {settings.TYPESENSE_COLLECTION!r}")
        except ObjectNotFound:
            ok(f"{label}: authenticated (collection not created yet — expected before first import)")
        except (RequestUnauthorized, RequestForbidden):
            fail(
                f"{label}: 401/403 — invalid, revoked, or scoped to another collection",
                "regenerate the key in the TypeSense Cloud dashboard",
            )
            healthy = False
        except Exception as exc:  # noqa: BLE001
            fail(f"{label}: {type(exc).__name__}: {exc}")
            healthy = False

    return healthy


# --------------------------------------------------------------------------
# 5. Source table
# --------------------------------------------------------------------------


async def check_source_table() -> bool:
    section("5. Postgres source table")
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.utils.facility_mapper import SOURCE_COLUMNS, SOURCE_TABLE

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'Final Table'"
                )
            )
            available = {row[0] for row in result.all()}

            if not available:
                fail(
                    f"{SOURCE_TABLE} not found or not readable",
                    "check the table name, schema, and database credentials",
                )
                return False
            ok(f"{SOURCE_TABLE} found ({len(available)} columns)")

            missing = [col for col in SOURCE_COLUMNS if col not in available]
            if missing:
                fail(
                    f"columns the mapper needs are missing: {', '.join(missing)}",
                    "the source schema changed — update SOURCE_COLUMNS in facility_mapper.py",
                )
                return False
            ok(f"all {len(SOURCE_COLUMNS)} mapped columns present")

            count = (
                await session.execute(text('SELECT COUNT(*) FROM public."Final Table"'))
            ).scalar_one()
            ok(f"{count:,} rows")

            nulls = (
                await session.execute(
                    text(
                        'SELECT COUNT(*) FROM public."Final Table" '
                        'WHERE "updated_at" IS NULL'
                    )
                )
            ).scalar_one()
            if nulls:
                warn(f"{nulls:,} rows have NULL updated_at — incremental sync will always skip them")
            else:
                ok("updated_at populated on every row — incremental sync will work")
            return True
    except Exception as exc:  # noqa: BLE001
        fail(
            f"cannot query Postgres — {type(exc).__name__}: {exc}",
            "check DATABASE_URL / ASYNC_DATABASE_URL",
        )
        return False


# --------------------------------------------------------------------------
# 6. Mapper
# --------------------------------------------------------------------------


async def check_mapper() -> bool:
    """Map REAL rows, so the check reflects the actual data rather than a fixture."""
    section("6. Mapper (against real rows)")
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.utils.facility_mapper import build_select_sql, to_documents

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(build_select_sql()), {"after_uuid": None, "since": None, "limit": 200}
            )
            rows = [dict(row) for row in result.mappings()]
    except Exception as exc:  # noqa: BLE001
        fail(f"could not read sample rows — {type(exc).__name__}: {exc}")
        return False

    if not rows:
        fail("the source table returned no rows")
        return False

    documents, errors = to_documents(rows)
    ok(f"mapped {len(documents)}/{len(rows)} sample rows")

    if errors:
        warn(f"{len(errors)} rows unmappable (no uuid or no name) — they will not be searchable")
        for message in errors[:3]:
            info(message)

    if not documents:
        fail("no rows produced a valid document")
        return False

    coverage: Counter[str] = Counter()
    for doc in documents:
        coverage.update(doc.keys())
    total = len(documents)

    info("field coverage across the sample:")
    for field in (
        "name", "city", "state", "zip_code", "address",
        "facility_type_category", "ownership_type",
        "latitude", "overall_rating", "bed_count",
    ):
        pct = coverage.get(field, 0) / total * 100
        info(f"  {field:<24} {pct:5.1f}%  {'#' * int(pct / 5)}")

    sample = documents[0]
    info(f"sample: {sample.get('name')!r} — {sample.get('city')}, {sample.get('state')}")
    return True


# --------------------------------------------------------------------------
# 7. Collection
# --------------------------------------------------------------------------


async def check_collection() -> tuple[bool, int]:
    section("7. TypeSense collection")
    from app.core.config import settings
    from app.core.typesense import get_typesense_client, run_typesense, use_admin_credentials
    from app.services.typesense_collection_service import build_schema, get_collection_stats

    # This process only reads, but reading the collection schema is itself an
    # admin operation.
    use_admin_credentials()

    try:
        stats = await get_collection_stats()
    except Exception as exc:  # noqa: BLE001
        fail(f"could not read the collection — {type(exc).__name__}: {exc}")
        return False, 0

    if stats is None:
        warn(f"collection {settings.TYPESENSE_COLLECTION!r} does not exist yet")
        info("expected before the first import")
        return True, 0

    ok(f"collection {stats['name']!r} exists")
    ok(f"{stats['num_documents']:,} documents indexed")

    expected_fields = {f["name"] for f in build_schema()["fields"]}
    client = get_typesense_client()
    live = await run_typesense(client.collections[settings.TYPESENSE_COLLECTION].retrieve)
    live_fields = {f["name"] for f in live.get("fields", [])}

    missing = expected_fields - live_fields
    if missing:
        fail(
            f"the live collection is missing fields: {', '.join(sorted(missing))}",
            "schema changed — run: python -m scripts.typesense_import --full --recreate",
        )
        return False, stats["num_documents"]

    ok(f"schema matches ({len(expected_fields)} fields)")
    return True, stats["num_documents"]


# --------------------------------------------------------------------------
# 8. API wiring
# --------------------------------------------------------------------------


def check_api_wiring() -> bool:
    """
    Verify the endpoint file is wired correctly.

    Two failure modes here are silent rather than loud:

    * A duplicate route — FastAPI keeps the FIRST registration, so a leftover
      old handler wins, the TypeSense path is never called, and nothing errors.
    * "/{facility_id}" declared before "/search" — the catch-all swallows the
      literal paths and every search 400s with "Invalid facility id".
    """
    section("8. API wiring")
    try:
        from app.api.v1.endpoints.facilities import router
    except Exception as exc:  # noqa: BLE001
        fail(f"could not import the facilities endpoint — {type(exc).__name__}: {exc}")
        return False

    routes = [(r.path, tuple(sorted(r.methods))) for r in router.routes]
    healthy = True

    # The router is built with prefix="/facilities", and FastAPI applies that
    # when the route is added — so `route.path` is "/facilities/search", not
    # "/search". Comparing against bare paths reports every route as missing,
    # and worse, makes the catch-all ordering check pass vacuously because
    # "/{facility_id}" is not in the list either.
    prefix = getattr(router, "prefix", "") or ""
    paths = [path for path, _ in routes]

    def registered(path: str) -> bool:
        return f"{prefix}{path}" in paths or path in paths

    duplicates = [path for path, count in Counter(routes).items() if count > 1]
    if duplicates:
        fail(
            f"duplicate routes registered: {duplicates}",
            "an old copy of a handler is still in facilities.py — delete it",
        )
        healthy = False
    else:
        ok(f"{len(routes)} routes, no duplicates{f' (prefix {prefix!r})' if prefix else ''}")

    catch_all = f"{prefix}/{{facility_id}}"
    if catch_all not in paths:
        fail(f"route {catch_all} is not registered")
        healthy = False
    elif paths[-1] != catch_all:
        fail(
            f"'{catch_all}' is not declared last — it will swallow /search and /suggest",
            "move get_facility_detail to the bottom of facilities.py",
        )
        healthy = False
    else:
        ok(f"catch-all '{catch_all}' is declared last")

    for required in ("/search", "/suggest", "/recommended"):
        if registered(required):
            ok(f"route {prefix}{required} registered")
        else:
            fail(f"route {prefix}{required} is not registered")
            healthy = False

    # Confirm the endpoint actually references the TypeSense path and its
    # fallback. A plain substring scan of the source is enough here — we are
    # checking that the wiring exists, not analysing control flow.
    import inspect

    from app.api.v1.endpoints import facilities as module

    source = inspect.getsource(module)
    for marker, label in (
        ("typesense_search_service", "TypeSense wired into the endpoint"),
        ("_search_facilities_postgres", "Postgres fallback present"),
        ("TypesenseUnavailable", "fallback exception handling present"),
    ):
        if marker in source:
            ok(label)
        else:
            fail(f"{label} — NOT found in facilities.py", "replace it with the final version")
            healthy = False

    return healthy


# --------------------------------------------------------------------------
# 9. Live search
# --------------------------------------------------------------------------


async def check_live_search(document_count: int) -> bool:
    section("9. Live search")
    if document_count == 0:
        warn("index is empty — skipping (run the import first)")
        return True

    from app.services import typesense_search_service as svc

    queries: list[tuple[str, dict[str, Any]]] = [
        ("filters only (browse)", {"state": "CA"}),
        ("city", {"city": "Napa"}),
        ("name with a typo", {"name": "hospise"}),
        ("mid-word (infix)", {"name": "ospice"}),
        ("single box: city", {"q": "napa"}),
        ("single box: zip", {"q": "94559"}),
        ("single box: mixed", {"q": "hospice california"}),
    ]

    healthy = True
    for label, params in queries:
        try:
            result = await svc.search_facilities(page=1, page_size=3, **params)
            found = result["total"]
            first = result["items"][0]["name"][:38] if result["items"] else "-"
            (ok if found else warn)(f"{label:<24} found={found:<8,} e.g. {first}")
            if not found:
                healthy = False
        except Exception as exc:  # noqa: BLE001
            fail(f"{label}: {type(exc).__name__}: {exc}")
            healthy = False

    return healthy


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


async def run(skip_db: bool) -> int:
    print(f"\n{B}TypeSense integration doctor{X}")
    print("=" * 62)

    # Each gate stops the run: continuing would test the wrong code, or bury
    # the real cause under cascading failures.
    if not check_dependency():
        return 2
    if not check_modules():
        print(f"\n{R}Fix the modules above first — every later check would be "
              f"testing code that is not actually running.{X}\n")
        return 1
    if not check_env():
        print(f"\n{R}Fix the environment above first.{X}\n")
        return 1
    if not check_keys():
        print(f"\n{R}Fix the API keys above first.{X}\n")
        return 1

    if skip_db:
        warn("--skip-db: source table and mapper checks skipped")
    else:
        await check_source_table()
        await check_mapper()

    _, document_count = await check_collection()
    check_api_wiring()
    await check_live_search(document_count)

    print("\n" + "=" * 62)
    if _problems:
        print(f"{R}{B}{len(_problems)} problem(s):{X}\n")
        for index, problem in enumerate(_problems, 1):
            print(f"  {index}. {problem}")
        print()
        return 1

    print(f"{G}{B}Everything checks out.{X}\n")
    if document_count == 0:
        print("  Next:  python -m scripts.typesense_import --full --dry-run")
        print("  Then:  python -m scripts.typesense_import --full\n")
    else:
        print(f"  Index holds {document_count:,} documents and search is working.")
        print("  Keep it current with:")
        print("    python -m scripts.typesense_import --incremental   (every 15 min)")
        print("    python -m scripts.typesense_import --reconcile     (nightly)\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the whole TypeSense integration.")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="skip the Postgres source-table and mapper checks",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.skip_db))
    except KeyboardInterrupt:
        print("\n  Interrupted.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())