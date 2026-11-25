// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// The framework-free entry of the shared map engine. The React binding is a separate entry (./react), so a page
// that only needs the geometry never pulls React hooks in. Apps import this folder, never TripPlanner app code.
export * from "./geo";
export * from "./motion";
export * from "./layers";
export * from "./engine";
