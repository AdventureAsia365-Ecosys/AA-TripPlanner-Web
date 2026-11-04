// AA-1029 — the tour page "from" price: formatted from the Booking contract price.from.v1, hidden without one.
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatFromPrice, fromPriceOffer, type FromPrice } from "./fromPrice.ts";

const P: FromPrice = { amount: "1235.00", currency: "USD", basis: "per_person", pax: 2, season: "Low", valid_to: "2027-03-31" };

test("formats whole units, en-US", () => {
  assert.equal(formatFromPrice(P), "$1,235");
  assert.equal(formatFromPrice({ ...P, currency: "EUR" }), "€1,235");
});

test("no price, a bad amount or currency shows nothing", () => {
  assert.equal(formatFromPrice(null), null);
  assert.equal(formatFromPrice(undefined), null);
  assert.equal(formatFromPrice({ ...P, amount: "0" }), null);
  assert.equal(formatFromPrice({ ...P, amount: "x" }), null);
  assert.equal(formatFromPrice({ ...P, currency: "usd" }), null);
});

test("the JSON-LD offer carries the price and its validity", () => {
  const offer = fromPriceOffer(P, "https://example.test/tours/x");
  assert.equal(offer?.price, "1235.00");
  assert.equal(offer?.priceCurrency, "USD");
  assert.equal(offer?.priceValidUntil, "2027-03-31");
  assert.equal(fromPriceOffer(null, "u"), undefined);
});
