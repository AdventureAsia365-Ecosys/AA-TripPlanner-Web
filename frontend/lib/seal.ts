// AA-908 — sealed (encrypted + authenticated) cookie values for the traveller session. Server-only.
// Adapted from AA-Partner web/lib/seal.ts (AA-935): AES-256-GCM from node:crypto; one key per purpose derived with
// HKDF-SHA256 from TP_SESSION_SECRET, and the purpose is also the additional authenticated data, so a value sealed
// for one cookie never opens as another. Only the purposes and the HKDF salt differ from the Partner file.
//
// Format (base64url): version byte 0x01 | 12-byte IV | ciphertext | 16-byte tag. The plaintext is JSON with an
// `exp` (epoch seconds); open() refuses an expired value, a wrong key, a wrong purpose or any changed byte, and
// returns null rather than throwing (a bad cookie is just "no session").

import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from "node:crypto";

// "session": the access token cookie; "refresh": the refresh token cookie; "signin": the pending email-code step.
export type Purpose = "session" | "refresh" | "signin";

const VERSION = 1;
const IV_BYTES = 12;
const TAG_BYTES = 16;
const SALT = Buffer.from("aa-tripplanner-web/AA-908/v1");

function key(secret: string, purpose: Purpose): Buffer {
  return Buffer.from(hkdfSync("sha256", Buffer.from(secret, "utf8"), SALT, Buffer.from(purpose), 32));
}

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

export function seal(secret: string, purpose: Purpose, value: Record<string, unknown> & { exp: number }): string {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key(secret, purpose), iv);
  cipher.setAAD(Buffer.from(purpose));
  const body = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([Buffer.from([VERSION]), iv, body, cipher.getAuthTag()]).toString("base64url");
}

export function open<T extends { exp: number }>(
  secret: string,
  purpose: Purpose,
  sealed: string | undefined | null,
  now: number = nowSeconds(),
): T | null {
  if (!sealed || sealed.length > 8192) return null;
  try {
    const raw = Buffer.from(sealed, "base64url");
    if (raw.length < 1 + IV_BYTES + TAG_BYTES + 2 || raw[0] !== VERSION) return null;
    const iv = raw.subarray(1, 1 + IV_BYTES);
    const tag = raw.subarray(raw.length - TAG_BYTES);
    const body = raw.subarray(1 + IV_BYTES, raw.length - TAG_BYTES);
    const decipher = createDecipheriv("aes-256-gcm", key(secret, purpose), iv);
    decipher.setAAD(Buffer.from(purpose));
    decipher.setAuthTag(tag);
    const plain = Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8");
    const value = JSON.parse(plain) as T;
    if (typeof value?.exp !== "number" || value.exp <= now) return null;
    return value;
  } catch {
    return null;
  }
}
