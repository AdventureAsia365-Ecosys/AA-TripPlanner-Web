// AA-1041 (R7 item 4) — saves the planner's reorders one after another (lib/useTrip.tsx saveOrder).
//
// Quick moves (arrow keys pressed fast on a drag handle, several drags) each draw their new order at once and each
// send one reorder (the full component id list). Without an order on those saves, an answer that lands late would
// draw an older order over a newer one, the next move would start from that older order, and two saves reaching the
// trip Lambda out of order could store an order the screen does not show. So:
//   - the saves run one after another (a promise chain): the server receives them in the order they were made, and
//     the last one sent is the one stored;
//   - each move starts from the latest order drawn (not from a server answer that may be older);
//   - only the answer of the latest move is drawn; the answers of earlier moves only update the order known to be
//     stored;
//   - a failed or refused latest move draws the last order the server stored and reports the failure;
//   - undo goes back to the order the server stored before the latest move (never to an order it never stored);
//   - invalidate() (a stop added or removed while saves are in flight) drops the answers still to come, and the next
//     move starts from the itinerary as it is then.
// Pure and import-free so node --test runs it (lib/orderSaver.test.ts).

export type SaveOutcome = { ok: true; undo: string[] | null } | { ok: false };

export interface OrderSaverDeps<T extends { component_id: string }> {
  /** Sends one order; resolves to the server's itinerary, or null when the save was refused. May reject. */
  send: (ids: string[]) => Promise<T[] | null | undefined>;
  /** The optimistic redraw of an itinerary in a new order (lib/itineraryOrder.ts reorderLocally). */
  local: (base: T[], ids: string[]) => T[];
  /** Draws an itinerary. */
  show: (itinerary: T[]) => void;
  /** Called once the latest move has an answer. */
  settled: (outcome: SaveOutcome) => void;
}

export interface OrderSaver<T> {
  /** Draws the new order at once and queues its save. `current` is the itinerary on screen when no save is running. */
  save: (current: T[], ids: string[], isUndo: boolean) => Promise<void>;
  /** Drops the answers of the saves in flight (the itinerary changed by another way). */
  invalidate: () => void;
}

const ids = (itinerary: readonly { component_id: string }[]) => itinerary.map((d) => d.component_id);

export function createOrderSaver<T extends { component_id: string }>(deps: OrderSaverDeps<T>): OrderSaver<T> {
  let seq = 0;
  // Saves numbered up to here were invalidated: their answers are not used at all.
  let floor = 0;
  let pending = 0;
  let chain: Promise<void> = Promise.resolve();
  // The latest order drawn by a move still being saved (null: none, start from the itinerary on screen).
  let drawn: T[] | null = null;
  // The last order the server is known to store.
  let stored: T[] = [];

  return {
    save(current, orderedIds, isUndo) {
      if (pending === 0 || drawn === null) {
        stored = current;
        drawn = current;
      }
      const mine = ++seq;
      pending += 1;
      const optimistic = deps.local(drawn, orderedIds);
      drawn = optimistic;
      deps.show(optimistic);
      const run = chain.then(async () => {
        let answer: T[] | null | undefined;
        try {
          answer = await deps.send(orderedIds);
        } catch {
          answer = null;
        }
        pending -= 1;
        const latest = mine === seq;
        if (mine <= floor) {
          // Invalidated: the itinerary changed by another way since this move.
        } else if (answer) {
          const before = stored;
          stored = answer;
          if (latest) {
            drawn = answer;
            deps.show(answer);
            deps.settled({ ok: true, undo: isUndo ? null : ids(before) });
          }
        } else if (latest) {
          drawn = stored;
          deps.show(stored);
          deps.settled({ ok: false });
        }
        if (pending === 0) drawn = null;
      });
      chain = run;
      return run;
    },
    invalidate() {
      seq += 1;
      floor = seq;
      drawn = null;
    },
  };
}
