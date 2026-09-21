#!/usr/bin/env bash
# Regenerate the content-handoff CSV of destinations needing a cover image.
# Requires the DB tunnel up and TRIPPLANNER_DATABASE_URL set (or edit below).
#
#   bash docs/content-handoff/export.sh
#
set -euo pipefail

DSN="${TRIPPLANNER_DATABASE_URL:-postgresql://aa_cis_admin@localhost:15432/aa_cis_dev}"
OUT="$(dirname "$0")/destinations_cover_images.csv"

psql "$DSN" -c "\copy (SELECT id AS destination_id, name AS destination_name, country, COALESCE(cover_image_url,'') AS current_cover_image_url, '' AS new_cover_image_url, '' AS photo_credit, '' AS notes FROM shared.destinations ORDER BY country, name) TO '$OUT' WITH (FORMAT csv, HEADER true)"

echo "Wrote $OUT ($(wc -l < "$OUT") lines incl. header)."
