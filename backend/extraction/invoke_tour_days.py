"""AA-675 — operator script: rebuild tripplanner.tour_day through the assembly Lambda.

The Lambda holds the Bedrock satellite role, the DB secret and the Mapbox secret; this script only
needs `lambda:InvokeFunction` (aa365-admin). One tour per invoke keeps each call inside the 60 s
timeout. Unchanged tours are skipped by the Lambda (same itinerary hash) unless --force.
Afterwards it rebuilds the Tour Graph (AA-673, op "tour_graph") unless --no-graph.

    python -m backend.extraction.invoke_tour_days [--only-stale] [--force] [--country Bhutan] [--limit 5] [--no-graph]
    python -m backend.extraction.invoke_tour_days --relink    # re-locate every night anchor, then rebuild the graph
"""
from __future__ import annotations

import argparse
import json

FUNCTION = "aa-tripplanner-dev-assembly"


def _invoke(client, payload: dict) -> dict:
    resp = client.invoke(FunctionName=FUNCTION, Payload=json.dumps({"extraction": payload}).encode())
    body = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise RuntimeError(f"Lambda error: {body}")
    return body


def main() -> None:
    import boto3

    ap = argparse.ArgumentParser()
    ap.add_argument("--only-stale", action="store_true", help="skip tours whose text is unchanged")
    ap.add_argument("--force", action="store_true", help="re-extract even when unchanged")
    ap.add_argument("--country", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-graph", action="store_true", help="skip the Tour Graph rebuild (AA-673)")
    ap.add_argument("--components", action="store_true",
                    help="AA-674: rebuild itinerary_components (from atoms) instead of tour_day")
    ap.add_argument("--relink", action="store_true", help="only re-locate night anchors (op relink), page by page")
    ap.add_argument("--pages", type=int, default=0, help="with --relink: stop after N pages (a trial)")
    ap.add_argument("--junction-km", type=float, default=150.0)
    ap.add_argument("--profile", default="aa365-admin")
    args = ap.parse_args()

    client = (boto3.Session(profile_name=args.profile, region_name="us-west-1") if args.profile else boto3.Session(region_name="us-west-1")).client("lambda")
    if args.relink:
        after, pages, totals = "", 0, {"mapbox": 0, "llm": 0, "unlinked": 0}
        while True:
            r = _invoke(client, {"op": "relink", "after": after})
            if "error" in r:
                raise SystemExit(r)
            for k in totals:
                totals[k] += r[k]
            print(f"relink page {r['page']} ({r['seconds']}s) mapbox={r['mapbox']} llm={r['llm']} "
                  f"unlinked={r['unlinked']} last={r['last']!r}")
            pages += 1
            if r["done"] or not r["last"] or (args.pages and pages >= args.pages):
                break
            after = r["last"]
        print("relink totals", totals, "anchors", r["anchors"], "nights without anchor", r["unanchored_nights"])
        tours = []
    else:
        tours = _invoke(client, {"op": "list"})["tours"]
    if args.country:
        tours = [t for t in tours if (t["country"] or "").lower() == args.country.lower()]
    if args.only_stale and not args.force and not args.components:
        tours = [t for t in tours if t["stale"]]
    if args.limit:
        tours = tours[: args.limit]
    if tours or not args.relink:
        print(f"{len(tours)} tours to process")
    totals = {"days": 0, "overnight": 0, "anchored": 0, "linked": 0, "errors": 0}
    for i, t in enumerate(tours, 1):
        try:
            if args.components:
                r = _invoke(client, {"op": "components", "tour_ids": [t["tour_id"]]})
                per = r.get("per_tour", {}).get(t["tour_id"])
                print(f"[{i}/{len(tours)}] {t['name'][:50]:<50} {r.get('seconds')}s {per}")
                continue
            r = _invoke(client, {"op": "tour_days", "tour_ids": [t["tour_id"]], "force": args.force})
            per = r.get("per_tour", {}).get(t["tour_id"])
            if per:
                for k in ("days", "overnight", "anchored", "linked"):
                    totals[k] += per[k]
            print(f"[{i}/{len(tours)}] {t['name'][:50]:<50} {r.get('seconds')}s {per or 'skipped (unchanged)'}")
        except Exception as e:  # noqa: BLE001 — keep going, report at the end
            totals["errors"] += 1
            print(f"[{i}/{len(tours)}] {t['name'][:50]:<50} ERROR {str(e)[:200]}")
    if tours:
        print("totals", totals)
    if not args.no_graph:
        r = _invoke(client, {"op": "tour_graph", "junction_km": args.junction_km})
        print("tour_graph", r.get("seconds"), "s", json.dumps(r.get("stats"), indent=1))


if __name__ == "__main__":
    main()
