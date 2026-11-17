"""AA-1115 (root ADR 0019 d10, S282 audit 6.3): no text the backend sends to a traveller names Adventure Asia.

An agency's planner (/{slug}/plan, and its own domain) shows the agency's brand only. The frontend guard
(frontend/lib/agencyBrand.test.ts) walks the pages' imports, so it cannot see the sentences this backend puts in its
answers: the chat's server replies (plan_chat.fallback_reply, SERVER_PROPOSAL_NOTE), the coverage gap reason
(coverage.py, shown in the trip panel) and the suggestion "why" (suggestions.py, the tooltip of "You might also like").

This guard reads every string literal of the traveller-facing packages (backend/assembly, backend/browse), docstrings
excluded, and fails on "Adventure Asia", "adventure.asia" or "AA" as a word, unless the allow-list below says why that
string never reaches an agency's traveller. Offline, invented names only.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.assembly import plan_chat

BACKEND = Path(__file__).resolve().parents[1]
PACKAGES = ("assembly", "browse")
BRAND_RE = re.compile(r"adventure[\s ]+asia|adventure\.asia", re.IGNORECASE)
AA_WORD_RE = re.compile(r"(?<![\w-])AA(?![\w-])")

# (file, a fragment of the string) -> why an agency's traveller never sees it.
ALLOWED = {
    ("assembly/agent.py", "You are an Adventure Asia trip narrator"):
        "the tp_compose / renarrate model prompt, never shown; changing it moves the prompt hash (follow-up)",
    ("assembly/plan_chat.py", "You are the trip planning assistant of Adventure Asia"):
        "the planning chat's system prompt, never shown; changing it moves the prompt (follow-up)",
    ("assembly/plan_chat.py", "Countries Adventure Asia sells tours in"):
        "the planning chat's user prompt line, never shown (same follow-up as the system prompt)",
    ("assembly/plan_chat.py", "Adventure Asia"):
        "_OWN_PHRASES: a name a model reply may use without a catalog row (grounding check), not a text we send",
    ("assembly/plan_chat.py", "adventure asia aa ai"):
        "the grounding check's lower-case word list, not a text we send",
    ("assembly/handler.py", "This planner is a demo: Adventure Asia works with travel agencies"):
        "the send-to-advisor answer of the bare /plan and the AA demo tenant only; an agency's planner sends a lead",
    ("assembly/leads.py", "Adventure Asia keeps no copy of this request"):
        "the lead e-mail to the agency (the controller), not to the traveller",
    ("assembly/notify.py", "[AA TripPlanner] New trip to review"):
        "the staff hand-off subject of AA's own planner, logged only",
    ("browse/catalog.py", "Adventure Asia catalog writer"):
        "the generator name of the AI mark data; the pages read only its time and edit flag (lib/tenantTours.ts)",
    ("browse/tenant_tours.py", "Adventure Asia content writer"):
        "the generator name of the AI mark data; the pages read only its time and edit flag (lib/tenantTours.ts)",
}


def _literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def _allowed(rel: str, text: str) -> bool:
    return any(rel == f and fragment in text for (f, fragment) in ALLOWED)


def test_no_traveller_text_of_the_backend_names_the_brand():
    offenders = []
    for package in PACKAGES:
        for path in sorted((BACKEND / package).rglob("*.py")):
            rel = path.relative_to(BACKEND).as_posix()
            for line, text in _literals(path):
                if (BRAND_RE.search(text) or AA_WORD_RE.search(text)) and not _allowed(rel, text):
                    offenders.append(f"{rel}:{line}: {text[:80]!r}")
    assert offenders == [], "use brand-neutral words (an agency's planner shows them) or allow-list with a reason"


def test_the_allow_list_is_still_needed():
    for (rel, fragment), why in ALLOWED.items():
        texts = [t for _, t in _literals(BACKEND / rel)]
        assert any(fragment in t for t in texts), f"{rel}: {fragment!r} is gone, drop it from ALLOWED"
        assert len(why) > 30


OFFERED = [{"country": "Norland", "tours": 4, "min_days": 2, "max_days": 9},
           {"country": "Tavora", "tours": 2, "min_days": 3, "max_days": 6}]


def test_the_chat_server_replies_name_no_brand():
    replies = [plan_chat.fallback_reply(OFFERED, with_proposal=True),
               plan_chat.fallback_reply(OFFERED, with_proposal=False),
               plan_chat.fallback_reply(OFFERED, with_proposal=False, failed=True),
               plan_chat.fallback_reply([], with_proposal=False),
               plan_chat.SERVER_PROPOSAL_NOTE]
    for reply in replies:
        assert not BRAND_RE.search(reply), reply
        assert not AA_WORD_RE.search(reply), reply
    assert "Norland and Tavora" in replies[1]
