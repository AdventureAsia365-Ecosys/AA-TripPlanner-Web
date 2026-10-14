// AA-812 hotfix (D1) — bounded tile loading for the planner map.
//
// The map used to ask for one 1-degree tile per cell of the viewport, all at once (Promise.all).
// A world view at zoom 3 covers ~90 x 70 degrees, i.e. ~6,350 requests per refresh: on the prod
// alias /plan sent ~10,200 tile requests in 8 s (503s, ERR_INSUFFICIENT_RESOURCES, Lambda
// throttling) and drew no pins, because one failed fetch rejected the whole Promise.all.
//
// This module keeps every view to a small, bounded number of requests:
//   - planTiles(): picks the smallest cell size (1..90 degrees) that covers the view in at most
//     MAX_TILES cells, so a world view is a handful of coarse tiles and a city view 1-degree ones.
//   - TileLoader: caches tiles in memory (per filter set), dedupes, runs at most `concurrency`
//     requests at a time, aborts the previous view's requests when the view changes, treats a
//     failed tile as empty (never rejects the whole view), and has a request budget per time
//     window as a last guard against a re-render / effect loop.
//
// No imports on purpose: unit-tested with `node --test` (lib/tileLoader.test.ts).

// Allowed cell sizes in degrees; each divides 90 and 180. Keep in sync with
// backend/browse/tiles.py TILE_SIZES.
export const TILE_SIZES = [1, 2, 5, 10, 30, 90] as const;
export const MAX_TILES = 16;

export interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface TilePlan {
  size: number;
  ids: string[];
}

// Tile id understood by GET /browse/tiles/{id}: "<lat>_<lng>" for 1 degree (the historic form,
// same CDN keys as before), "<lat>_<lng>_<size>" for coarser cells.
export function tileId(lat: number, lng: number, size: number): string {
  return size === 1 ? `${lat}_${lng}` : `${lat}_${lng}_${size}`;
}

// Longitude ranges inside [-180, 180) covered by the view. Mapbox reports west < -180 or
// east > 180 when the view crosses the antimeridian or shows world copies.
function lngRanges(west: number, east: number): [number, number][] {
  if (!Number.isFinite(west) || !Number.isFinite(east)) return [];
  if (east - west >= 360) return [[-180, 180]];
  const w = ((((west + 180) % 360) + 360) % 360) - 180;
  const e = w + (east - west);
  if (e <= 180) return [[w, e]];
  return [
    [w, 180],
    [-180, e - 360],
  ];
}

function cells(lo: number, hi: number, size: number, min: number, max: number): number[] {
  const out: number[] = [];
  const start = Math.max(min, Math.floor(lo / size) * size);
  // A view edge exactly on a cell boundary does not pull in the next cell.
  const end = Math.min(max - size, Math.ceil(hi / size) * size - size);
  for (let c = start; c <= end; c += size) out.push(c);
  return out;
}

function cellsFor(b: Bounds, size: number): string[] {
  const south = Math.max(-90, Math.min(90, b.south));
  const north = Math.max(-90, Math.min(90, b.north));
  if (!(north > south)) return [];
  const lats = cells(south, north, size, -90, 90);
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const [w, e] of lngRanges(b.west, b.east)) {
    if (!(e > w)) continue;
    for (const lng of cells(w, e, size, -180, 180)) {
      for (const lat of lats) {
        const id = tileId(lat, lng, size);
        if (!seen.has(id)) {
          seen.add(id);
          ids.push(id);
        }
      }
    }
  }
  return ids;
}

// The tiles to load for a view: the finest grid that needs at most `maxTiles` requests.
// The coarsest grid (90 degrees) is at most 4 x 2 = 8 cells for any view, so the bound always holds.
export function planTiles(b: Bounds, maxTiles: number = MAX_TILES): TilePlan {
  for (const size of TILE_SIZES) {
    const ids = cellsFor(b, size);
    if (ids.length <= maxTiles) return { size, ids };
  }
  const size = TILE_SIZES[TILE_SIZES.length - 1];
  return { size, ids: cellsFor(b, size) };
}

// `ctx` is whatever load() was given (e.g. the filters the cache scope was built from), so the
// request and its cache key always describe the same filter set.
export type TileFetcher<T, C = undefined> = (
  id: string,
  signal: AbortSignal,
  ctx: C,
) => Promise<T[]>;

export interface TileLoadResult<T> {
  pins: T[];
  // false when a tile failed or the request budget skipped some tiles.
  complete: boolean;
  // When incomplete: how long until the budget window frees a slot (0 = retry any time).
  retryAfterMs: number;
}

export interface TileLoaderOptions {
  concurrency?: number;
  cacheSize?: number;
  // Loop guard: at most `budget` network requests per `budgetWindowMs`.
  budget?: number;
  budgetWindowMs?: number;
  now?: () => number;
  onBudgetExceeded?: () => void;
}

export class TileLoader<T, C = undefined> {
  private readonly fetcher: TileFetcher<T, C>;
  private readonly concurrency: number;
  private readonly cacheSize: number;
  private readonly budget: number;
  private readonly budgetWindowMs: number;
  private readonly now: () => number;
  private readonly onBudgetExceeded?: () => void;
  private readonly cache = new Map<string, T[]>(); // insertion order = LRU order
  private current: AbortController | null = null;
  private sent: number[] = [];
  requests = 0; // total network requests issued (for tests / diagnostics)

  constructor(fetcher: TileFetcher<T, C>, opts: TileLoaderOptions = {}) {
    this.fetcher = fetcher;
    this.concurrency = opts.concurrency ?? 4;
    this.cacheSize = opts.cacheSize ?? 256;
    this.budget = opts.budget ?? 64;
    this.budgetWindowMs = opts.budgetWindowMs ?? 10_000;
    this.now = opts.now ?? (() => Date.now());
    this.onBudgetExceeded = opts.onBudgetExceeded;
  }

  private takeBudget(): boolean {
    const t = this.now();
    this.sent = this.sent.filter((s) => t - s < this.budgetWindowMs);
    if (this.sent.length >= this.budget) return false;
    this.sent.push(t);
    return true;
  }

  private budgetRetryMs(): number {
    if (this.sent.length < this.budget) return 0;
    return Math.max(0, this.sent[0] + this.budgetWindowMs - this.now());
  }

  private remember(key: string, pins: T[]) {
    this.cache.delete(key);
    this.cache.set(key, pins);
    while (this.cache.size > this.cacheSize) {
      const oldest = this.cache.keys().next().value;
      if (oldest === undefined) break;
      this.cache.delete(oldest);
    }
  }

  // Load the given tiles (cache key = scope + id, scope = the filter query string). Resolves with
  // the pins of every tile it could load, or null when a newer load() / abort() superseded it.
  async load(ids: string[], scope = "", ctx?: C): Promise<T[] | null> {
    const r = await this.loadView(ids, scope, ctx as C);
    return r ? r.pins : null;
  }

  // Same as load(), but also says whether every tile loaded, so the caller can keep its previous
  // pins and retry instead of drawing a partial view.
  async loadView(ids: string[], scope: string, ctx: C): Promise<TileLoadResult<T> | null> {
    this.current?.abort();
    const ctrl = new AbortController();
    this.current = ctrl;
    const unique = Array.from(new Set(ids));
    const out: T[] = [];
    const missing: string[] = [];
    for (const id of unique) {
      const hit = this.cache.get(`${scope}|${id}`);
      if (hit) {
        this.remember(`${scope}|${id}`, hit);
        out.push(...hit);
      } else {
        missing.push(id);
      }
    }
    let next = 0;
    let budgetHit = false;
    let failed = false;
    const worker = async () => {
      while (next < missing.length && !ctrl.signal.aborted) {
        if (!this.takeBudget()) {
          budgetHit = true;
          return;
        }
        const id = missing[next++];
        this.requests++;
        try {
          const pins = await this.fetcher(id, ctrl.signal, ctx);
          if (ctrl.signal.aborted) return;
          this.remember(`${scope}|${id}`, pins);
          out.push(...pins);
        } catch {
          // A failed or aborted tile counts as empty; it is not cached, so a later view retries it.
          failed = true;
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(this.concurrency, missing.length) }, worker));
    if (budgetHit) this.onBudgetExceeded?.();
    if (ctrl.signal.aborted) return null;
    if (this.current === ctrl) this.current = null;
    const complete = !budgetHit && !failed;
    return { pins: out, complete, retryAfterMs: budgetHit ? this.budgetRetryMs() : 0 };
  }

  abort() {
    this.current?.abort();
    this.current = null;
  }
}
