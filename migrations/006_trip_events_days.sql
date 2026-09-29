-- 006: AA-674 / Jira PR-11 point 3 — the traveller can add and remove days on top of the AA legs.
--
-- New trip events: add_day {day_id, kind: extra_night | free_day | extend_end, after_component_id,
-- note} and remove_day {day_id}. An added day is not part of any tour: it is a customization the
-- advisor sees in the event log. Activity selection needs no new event: it is add_component /
-- remove_component of a real tour-day component.
--
-- Widens the CHECK constraint only; existing rows stay valid.

BEGIN;

ALTER TABLE tripplanner.trip_events DROP CONSTRAINT IF EXISTS trip_events_type_chk;
ALTER TABLE tripplanner.trip_events ADD CONSTRAINT trip_events_type_chk CHECK (event_type IN (
    'add_component', 'remove_component', 'reorder', 'sent', 'add_day', 'remove_day'
));

COMMIT;
