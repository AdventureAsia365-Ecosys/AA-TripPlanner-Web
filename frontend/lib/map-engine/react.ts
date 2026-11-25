// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// react.ts: the thin React binding of the map engine. useMapEngine() gives a container ref for a <div>, creates the
// engine once a token is configured (mapbox-gl is loaded on demand), runs `onReady` when the style has loaded (add the
// app's sources and layers there), and destroys the map on unmount. Everything else is the framework-free engine
// (engine.ts). Only `react` is imported: no Next.
"use client";

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createMapEngine, hasToken, loadMapboxGl, type MapEngine, type MapEngineOptions, type MapboxModule } from "./engine";

export interface UseMapEngineOptions extends Omit<MapEngineOptions, "container" | "accessToken"> {
  /** The public token from the app's runtime config; empty = no map (render the empty state). */
  accessToken: string | null | undefined;
  /** How to get the mapbox-gl module (default: a dynamic import). Tests pass a fake. */
  load?: () => Promise<MapboxModule>;
  /** Called once per map when its style has loaded: add sources and layers here. */
  onReady?: (engine: MapEngine) => void;
}

export interface MapEngineState {
  containerRef: RefObject<HTMLDivElement | null>;
  /** The engine once created (null without a token, while mapbox-gl loads, or after a load failure). */
  engine: MapEngine | null;
  /** The style has loaded and `onReady` has run. */
  ready: boolean;
  /** mapbox-gl could not be loaded or the map could not be created. */
  failed: boolean;
}

export function useMapEngine(opts: UseMapEngineOptions): MapEngineState {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [engine, setEngine] = useState<MapEngine | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  // The latest callbacks and camera options, read when the map is created (synced after each commit, never during
  // render: React Compiler lint).
  const optsRef = useRef(opts);
  useLayoutEffect(() => {
    optsRef.current = opts;
  });
  const token = hasToken(opts.accessToken) ? opts.accessToken.trim() : "";

  useEffect(() => {
    const container = containerRef.current;
    if (!token || !container) return;
    let cancelled = false;
    let created: MapEngine | null = null;
    const o = optsRef.current;
    (o.load ?? loadMapboxGl)()
      .then((gl) => {
        if (cancelled) return;
        created = createMapEngine(gl, {
          container,
          accessToken: token,
          style: o.style,
          center: o.center,
          zoom: o.zoom,
          onError: o.onError,
        });
        const mine = created;
        setEngine(mine);
        mine.onReady(() => {
          if (cancelled) return;
          optsRef.current.onReady?.(mine);
          setReady(true);
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.error("[map engine]", err instanceof Error ? err.message : err);
        setFailed(true);
      });
    return () => {
      cancelled = true;
      created?.destroy();
      setEngine(null);
      setReady(false);
      setFailed(false);
    };
  }, [token]);

  return { containerRef, engine, ready, failed };
}
