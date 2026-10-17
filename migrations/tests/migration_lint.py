"""Offline lint of the TripPlanner migrations (X1 plan S7, AA-852). Standard library only.

TripPlanner has no migration replay in CI; this lint is the PR-time guard for the rules that change
with the X1 owner transfer (tp-013) and mirrors the Booking migration guard (X1 S5):

  * file names `NNN_name.sql`, numbers unique (MIG-3);
  * from tp-007 (the first file of the registry convention): one `BEGIN; … COMMIT;` and a
    self-registration of its own `tp-NNN` key with `ON CONFLICT (version) DO NOTHING` (MIG-2);
  * from tp-013 (X1 role rules, the same files the AA-CIS-Infra DB Migrate role lint covers):
      - no role switch or role DDL: `SET [LOCAL|SESSION] ROLE`, `RESET ROLE`, `SESSION AUTHORIZATION`,
        `CREATE/ALTER/DROP ROLE|USER|GROUP`, `CREATE/ALTER/DROP EXTENSION` (roles/ in Infra, MIG-6);
        DB Migrate itself switches to the schema owner before the file (XD8);
      - no `SECURITY DEFINER` (MIG-6);
      - every `ALTER DEFAULT PRIVILEGES` names `FOR ROLE <migration role>` explicitly. The migration
        role is `aa_cis_admin` up to tp-013 and `tp_owner` from tp-014 (`migration_role`). tp-013 is
        the transfer itself: it may also carry the `FOR ROLE aa_cis_admin … REVOKE` rows that retire
        the old default privileges, and its `FOR ROLE tp_owner` GRANT rows restate them;
      - `OWNER TO` only in tp-013 and only `OWNER TO tp_owner`: from tp-014 every object is created by
        tp_owner already.

The AA-CIS-Infra runner lint (`db/run_migration.py --inspect`, `db/check_migration_files.py`) stays the
authority at copy and apply time; this file catches the TripPlanner-specific rules in the owner PR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^([0-9]{3})_[a-z0-9_]+\.sql$")

REGISTRY_FROM = 7        # tp-007 backfilled tp-001..006 and every later file registers itself
X1_FROM = 13             # tp-013: tripplanner objects owned by tp_owner (X1 S7)
TRANSFER = 13            # the one file that moves ownership
MASTER_ROLE = "aa_cis_admin"
OWNER_ROLE = "tp_owner"

ROLE_SWITCH_RES = (
    (re.compile(r"\bSET\s+(?:(?:SESSION|LOCAL)\s+)?ROLE\b"), "SET ROLE (DB Migrate switches to the owner itself, XD8)"),
    (re.compile(r"\bRESET\s+ROLE\b"), "RESET ROLE"),
    (re.compile(r"\bSESSION[\s_]+AUTHORIZATION\b"), "SESSION AUTHORIZATION"),
    (re.compile(r"\b(?:CREATE|ALTER|DROP)\s+(?:ROLE|USER|GROUP)\b(?!\s+MAPPING\b)"),
     "role DDL (belongs in AA-CIS-Infra db/migrations/roles/, MIG-6)"),
    (re.compile(r"\b(?:CREATE|ALTER|DROP)\s+EXTENSION\b"),
     "EXTENSION DDL (belongs in AA-CIS-Infra db/migrations/roles/)"),
    (re.compile(r"\bSECURITY\s+DEFINER\b"), "SECURITY DEFINER (MIG-6)"),
)
ADP_RE = re.compile(r"\bALTER\s+DEFAULT\s+PRIVILEGES\b(?P<rest>[^;]*)")
FOR_ROLE_RE = re.compile(r"^\s*FOR\s+(?:ROLE|USER)\s+(?P<roles>[A-Z0-9_$\",\s]+?)\s+(?:IN\s+SCHEMA|GRANT|REVOKE)\b")
OWNER_TO_RE = re.compile(r"\bOWNER\s+TO\s+(?P<role>\"[^\"]+\"|[A-Z0-9_$]+)")


@dataclass(frozen=True)
class Problem:
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"


def migration_role(number: int) -> str:
    """The role a file runs as on Dev: the master until the transfer, tp_owner after it (XD8)."""
    return MASTER_ROLE if number <= TRANSFER else OWNER_ROLE


def code_view(sql: str, keep_strings: bool = False) -> str:
    """Upper-cased SQL with comments removed and single-quoted string contents blanked, so text in a
    comment or a RAISE message is never read as a statement. Dollar-quoted bodies (DO blocks, functions)
    stay code: plpgsql can run the same statements. keep_strings: comments removed, case and string
    literals kept (for the registration check)."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            out.append(sql[i:j + 1] if keep_strings else "''")
            i = j + 1
            continue
        out.append(c)
        i += 1
    text = "".join(out)
    return text if keep_strings else text.upper()


def _roles(raw: str) -> list:
    return [r.strip().strip('"').lower() for r in raw.split(",") if r.strip()]


def lint_text(sql: str, number: int, name: str) -> list:
    """Problems of one file, given its number."""
    problems = []
    code = code_view(sql)
    if number >= REGISTRY_FROM:
        statements = [s.strip() for s in code.split(";") if s.strip()]
        if not statements or statements[0] != "BEGIN" or statements[-1] != "COMMIT":
            problems.append(Problem(name, "must be one `BEGIN; … COMMIT;` block (MIG-2)"))
        key = f"tp-{number:03d}"
        # [^;'] or a whole '…' literal: a description may contain a semicolon.
        piece = r"(?:[^;']|'(?:[^']|'')*')*?"
        registration = re.search(
            r"INSERT\s+INTO\s+shared\.schema_versions\b" + piece + r"VALUES\s*\(\s*'" + re.escape(key) + "'"
            + piece + r"ON\s+CONFLICT\s*\(\s*version\s*\)\s*DO\s+NOTHING", code_view(sql, keep_strings=True),
            re.IGNORECASE | re.DOTALL)
        if not registration:
            problems.append(Problem(name, f"must register its own key '{key}' in shared.schema_versions "
                                          f"with ON CONFLICT (version) DO NOTHING (MIG-2)"))
    if number < X1_FROM:
        return problems
    for rx, what in ROLE_SWITCH_RES:
        if rx.search(code):
            problems.append(Problem(name, f"{what} is not allowed in a TripPlanner migration from tp-{X1_FROM:03d}"))
    expected = migration_role(number)
    for m in ADP_RE.finditer(code):
        rest = m.group("rest")
        fr = FOR_ROLE_RE.match(rest)
        if not fr:
            problems.append(Problem(name, f"ALTER DEFAULT PRIVILEGES without FOR ROLE: write "
                                          f"`FOR ROLE {OWNER_ROLE} IN SCHEMA tripplanner` (X1)"))
            continue
        roles = _roles(fr.group("roles"))
        revoke = re.search(r"\bREVOKE\b", rest) is not None
        for role in roles:
            if number == TRANSFER and role == MASTER_ROLE and revoke:
                continue                    # tp-013 retires the old FOR ROLE aa_cis_admin rows
            if number == TRANSFER and role == OWNER_ROLE:
                continue                    # tp-013 restates them FOR ROLE tp_owner
            if role != expected or number == TRANSFER:
                problems.append(Problem(name, f"ALTER DEFAULT PRIVILEGES FOR ROLE {role}: from tp-{TRANSFER + 1:03d} "
                                              f"only FOR ROLE {OWNER_ROLE} (the owner that creates the objects); "
                                              f"FOR ROLE {MASTER_ROLE} only as tp-{TRANSFER:03d}'s REVOKE"))
    for m in OWNER_TO_RE.finditer(code):
        role = m.group("role").strip('"').lower()
        if number != TRANSFER:
            problems.append(Problem(name, f"OWNER TO {role}: from tp-{TRANSFER + 1:03d} objects are created by "
                                          f"{OWNER_ROLE} already (DB Migrate runs the file as the owner)"))
        elif role != OWNER_ROLE:
            problems.append(Problem(name, f"OWNER TO {role}: tp-{TRANSFER:03d} transfers to {OWNER_ROLE} only"))
    return problems


def migration_files(directory: Path = MIGRATIONS_DIR) -> list:
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def lint_all(directory: Path = MIGRATIONS_DIR) -> list:
    problems, seen = [], {}
    for path in migration_files(directory):
        m = NAME_RE.match(path.name)
        if not m:
            problems.append(Problem(path.name, "file name must be NNN_lower_snake.sql (MIG-3, DB Migrate PATH_RE)"))
            continue
        number = int(m.group(1))
        if number in seen:
            problems.append(Problem(path.name, f"number {number:03d} is also used by {seen[number]} (MIG-3)"))
        seen[number] = path.name
        problems.extend(lint_text(path.read_text(encoding="utf-8"), number, path.name))
    return problems
