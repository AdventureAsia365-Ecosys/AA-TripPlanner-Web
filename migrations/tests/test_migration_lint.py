"""The TripPlanner migration lint (X1 plan S7, AA-852): the repo's files pass, and each rule bites."""
from __future__ import annotations

from migration_lint import lint_all, lint_text, migration_role

REG = ("INSERT INTO shared.schema_versions (version, applied_at, description)\n"
       "VALUES ('tp-{n:03d}', now(), 'x')\nON CONFLICT (version) DO NOTHING;\n")


def _file(n: int, body: str) -> str:
    return f"-- Migration tp-{n:03d}: test\nBEGIN;\n{body}\n{REG.format(n=n)}COMMIT;\n"


def _messages(n: int, body: str) -> list:
    return [p.message for p in lint_text(_file(n, body), n, f"{n:03d}_x.sql")]


def test_repo_migrations_pass():
    assert [str(p) for p in lint_all()] == []


def test_migration_role_switches_after_the_transfer():
    assert migration_role(12) == "aa_cis_admin"
    assert migration_role(13) == "aa_cis_admin"
    assert migration_role(14) == "tp_owner"


def test_tp014_default_privileges_for_tp_owner_pass():
    assert _messages(14, "CREATE TABLE IF NOT EXISTS tripplanner.t (id bigserial PRIMARY KEY);\n"
                         "ALTER DEFAULT PRIVILEGES FOR ROLE tp_owner IN SCHEMA tripplanner\n"
                         "    GRANT SELECT ON TABLES TO tripplanner;") == []


def test_tp014_default_privileges_for_the_master_fail():
    msgs = _messages(14, "ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner "
                         "GRANT SELECT ON TABLES TO tripplanner;")
    assert any("FOR ROLE aa_cis_admin" in m for m in msgs)


def test_tp014_master_revoke_fails_too():
    msgs = _messages(14, "ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner "
                         "REVOKE SELECT ON TABLES FROM tripplanner;")
    assert any("FOR ROLE aa_cis_admin" in m for m in msgs)


def test_default_privileges_without_for_role_fail():
    msgs = _messages(14, "ALTER DEFAULT PRIVILEGES IN SCHEMA tripplanner GRANT SELECT ON TABLES TO tripplanner;")
    assert any("without FOR ROLE" in m for m in msgs)


def test_tp013_master_grant_fails_only_revoke_allowed():
    ok = _messages(13, "ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner "
                       "REVOKE SELECT ON TABLES FROM tripplanner;\n"
                       "ALTER DEFAULT PRIVILEGES FOR ROLE tp_owner IN SCHEMA tripplanner "
                       "GRANT SELECT ON TABLES TO tripplanner;\n"
                       "ALTER TABLE tripplanner.sessions OWNER TO tp_owner;")
    assert ok == []
    bad = _messages(13, "ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner "
                        "GRANT SELECT ON TABLES TO tripplanner;")
    assert any("FOR ROLE aa_cis_admin" in m for m in bad)


def test_owner_to_only_in_the_transfer_and_only_to_tp_owner():
    assert any("OWNER TO tp_owner" in m for m in _messages(14, "ALTER TABLE tripplanner.t OWNER TO tp_owner;"))
    assert any("OWNER TO aa_cis_admin" in m
               for m in _messages(13, "ALTER TABLE tripplanner.t OWNER TO aa_cis_admin;"))


def test_role_switches_and_role_ddl_fail():
    for body, needle in (
        ("SET LOCAL ROLE tp_owner;", "SET ROLE"),
        ("SET ROLE tp_owner;", "SET ROLE"),
        ("DO $$ BEGIN RESET ROLE; END $$;", "RESET ROLE"),
        ("SET SESSION AUTHORIZATION tp_owner;", "SESSION AUTHORIZATION"),
        ("CREATE ROLE x NOLOGIN;", "role DDL"),
        ("ALTER ROLE tripplanner CONNECTION LIMIT 5;", "role DDL"),
        ("CREATE EXTENSION IF NOT EXISTS vector;", "EXTENSION"),
        ("CREATE FUNCTION tripplanner.f() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$;",
         "SECURITY DEFINER"),
    ):
        assert any(needle in m for m in _messages(14, body)), body


def test_comments_and_strings_are_not_code():
    body = ("-- RESET ROLE and ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin in a comment\n"
            "DO $$ BEGIN RAISE NOTICE 'SET ROLE tp_owner; OWNER TO aa_cis_admin'; END $$;")
    assert _messages(14, body) == []


def test_registration_and_transaction_shape():
    n = 14
    no_reg = f"BEGIN;\nCREATE TABLE tripplanner.t (id int);\nCOMMIT;\n"
    assert any("register its own key 'tp-014'" in p.message for p in lint_text(no_reg, n, "014_x.sql"))
    commented = "BEGIN;\n-- " + REG.format(n=n).replace("\n", "\n-- ") + "\nCOMMIT;\n"
    assert any("register its own key" in p.message for p in lint_text(commented, n, "014_x.sql"))
    wrong_key = _file(n, "").replace("'tp-014'", "'tp-013'")
    assert any("register its own key" in p.message for p in lint_text(wrong_key, n, "014_x.sql"))
    no_tx = REG.format(n=n)
    assert any("BEGIN; … COMMIT;" in p.message for p in lint_text(no_tx, n, "014_x.sql"))
