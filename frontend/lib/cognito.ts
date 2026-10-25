// AA-908 (root ADR 0006 Phase 4, decisions 1 and 4) — the traveller's passwordless sign-in, driven by the BFF
// through the Cognito user-pool public API (option b). Server-only by convention; no Next imports, so `npm test`
// runs it with a fake fetch.
//
// Flow (one email code, never a password):
//   start(email):  SignUp (no password, signed with the BFF's sign-up proof, below) -> a sign-up code is mailed;
//                  the address already has a user -> InitiateAuth USER_AUTH, PREFERRED_CHALLENGE=EMAIL_OTP -> a
//                  sign-in code is mailed; that user never confirmed -> ResendConfirmationCode.
//                  The answer to the browser is the same in every case (no account enumeration).
//   verify(code):  sign-up  -> ConfirmSignUp, then InitiateAuth USER_AUTH with the session ConfirmSignUp returns
//                              (tokens with no second code); no session -> a sign-in code is mailed instead.
//                              A sign-up Session lives 3 minutes (auth_session_validity) while the code lives
//                              longer: when ConfirmSignUp refuses the stale Session, it is retried without one;
//                  sign-in  -> RespondToAuthChallenge EMAIL_OTP. Refused as not authorised while the 3-minute
//                              session cannot have expired yet: the address may be an abandoned sign-up that got a
//                              simulated challenge (prevent_user_existence_errors), so a new sign-up code is
//                              requested (ResendConfirmationCode). Both fallbacks wait for the live E2E to confirm
//                              the real Cognito answers (docs/implementation-notes/AA-908.md).
//   refresh:       GetTokensFromRefreshToken (refresh-token rotation is on for this client, so the answer can carry a
//                  new refresh token, which replaces the old one).
//   sign-out:      RevokeToken (the refresh token and the access tokens issued from it).
//
// The client is public (no secret). The BFF marks its calls for the Cognito web ACL (AA-CIS-Infra identity.tf, rule
// BffRatePerTravellerIp): `x-aa-traveller-bff` = TP_COGNITO_BFF_KEY and `X-AA-Traveller-IP` = the traveller's own
// address, so the per-IP sign-in limits count each traveller, not the shared Vercel egress address. Both headers go
// together or not at all: a keyed call without a traveller address would escape every per-person limit.
//
// Sign-up proof: the EXTERNAL pre sign-up trigger accepts a native SignUp only from this BFF (AA-CIS-Infra
// lambda_src/identity_triggers/external.py). Otherwise anyone could pre-register a traveller's address with a
// password through the public API and inherit the account once the traveller confirms it. SignUp therefore carries
// ValidationData bff_ts (unix seconds) and bff_proof = hex HMAC-SHA256(TP_COGNITO_SIGNUP_PROOF_KEY,
// "<email>\n<bff_ts>"); the trigger accepts it for 5 minutes. Never log an email, a code, a session or a token.

import { createHmac } from "node:crypto";

export const COGNITO_REGION = "us-west-1";
export const COGNITO_ENDPOINT = `https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`;
// aa-external-dev-tripplanner: a public client id (it is in every Cognito call), like backend/shared/identity_config.py.
export const TRAVELLER_CLIENT_ID = "2fqmuij0lf47qpf1phjfcgseds";
export const BFF_KEY_HEADER = "x-aa-traveller-bff";
export const TRAVELLER_IP_HEADER = "X-AA-Traveller-IP";

export type Mode = "signup" | "signin";

export interface Tokens {
  accessToken: string;
  refreshToken: string | null; // null on a refresh that did not rotate
  expiresIn: number; // seconds
}

// What the browser is told when something fails. Never the Cognito message (it can name the user).
export type ErrorCode =
  | "invalid_email"
  | "code_invalid"
  | "code_expired"
  | "try_later"
  | "sign_up_unavailable"
  | "delivery_failed"
  | "unavailable";

export class CognitoError extends Error {
  readonly type: string; // the Cognito exception name ("" when there was none, e.g. a WAF block)
  readonly status: number;

  // No parameter properties: `npm test` runs this file with type stripping only.
  constructor(type: string, status: number) {
    super(type || `cognito_http_${status}`);
    this.type = type;
    this.status = status;
  }
}

export interface CallContext {
  bffKey: string;
  signupProofKey?: string; // "" / absent: SignUp goes without a proof and the trigger refuses new addresses
  travellerIp: string | null;
  fetchImpl?: typeof fetch;
  clientId?: string;
  now?: () => number; // unix seconds (tests)
}

// Matches AA-CIS-Infra external.py signup_proof() (shared test vector in cognito.test.ts).
export function signUpProof(key: string, email: string, ts: string): string {
  return createHmac("sha256", key).update(`${email}\n${ts}`).digest("hex");
}

function nowS(ctx: CallContext): number {
  return ctx.now ? ctx.now() : Math.floor(Date.now() / 1000);
}

// How long a sign-in challenge Session lives (AA-CIS-Infra identity.tf auth_session_validity = 3 minutes), with a
// margin for clock and network.
export const AUTH_SESSION_S = 170;

export function errorCode(e: unknown): ErrorCode {
  if (!(e instanceof CognitoError)) return "unavailable";
  switch (e.type) {
    case "InvalidParameterException":
      return "invalid_email";
    case "CodeMismatchException":
      return "code_invalid";
    case "ExpiredCodeException":
    case "NotAuthorizedException": // an auth session that timed out (3 minutes) or was used up
      return "code_expired";
    case "TooManyRequestsException":
    case "LimitExceededException":
    case "TooManyFailedAttemptsException":
      return "try_later";
    case "UserLambdaValidationException": // the pre sign-up trigger refused (native sign-up off)
      return "sign_up_unavailable";
    case "CodeDeliveryFailureException":
      return "delivery_failed";
    default:
      // A WAF block answers 403 without an exception name.
      return e.status === 403 && !e.type ? "try_later" : "unavailable";
  }
}

const IPV4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:]{2,45}$/;

export interface HeaderSource {
  get(name: string): string | null;
}

// The traveller's own address. On Vercel the edge sets x-forwarded-for (and x-real-ip); a client cannot prepend to
// it there. The first entry is the client.
export function travellerIp(headers: HeaderSource): string | null {
  const first = (headers.get("x-forwarded-for") ?? headers.get("x-real-ip") ?? "").split(",")[0].trim();
  return IPV4.test(first) || (first.includes(":") && IPV6.test(first)) ? first : null;
}

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$/;

// Lower-cased and trimmed, or null when it is not an address (the pre sign-up trigger normalises the same way).
export function normaliseEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= 254 && EMAIL.test(email) ? email : null;
}

async function call(action: string, body: Record<string, unknown>, ctx: CallContext): Promise<any> {
  const headers: Record<string, string> = {
    "content-type": "application/x-amz-json-1.1",
    "x-amz-target": `AWSCognitoIdentityProviderService.${action}`,
  };
  if (ctx.bffKey && ctx.travellerIp) {
    headers[BFF_KEY_HEADER] = ctx.bffKey;
    headers[TRAVELLER_IP_HEADER] = ctx.travellerIp;
  }
  const res = await (ctx.fetchImpl ?? fetch)(COGNITO_ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!res.ok) {
    const raw = String(data?.__type ?? res.headers.get("x-amzn-errortype") ?? "");
    throw new CognitoError(raw.split("#").pop()!.split(":")[0], res.status);
  }
  return data;
}

function tokens(data: any): Tokens {
  const r = data?.AuthenticationResult;
  if (!r?.AccessToken) throw new CognitoError("NoTokens", 502);
  return {
    accessToken: String(r.AccessToken),
    refreshToken: r.RefreshToken ? String(r.RefreshToken) : null,
    expiresIn: Number(r.ExpiresIn) || 3600,
  };
}

// InitiateAuth USER_AUTH asking for an email code; a SELECT_CHALLENGE answer is answered with EMAIL_OTP.
async function requestSignInCode(email: string, ctx: CallContext): Promise<string> {
  const clientId = ctx.clientId ?? TRAVELLER_CLIENT_ID;
  let data = await call("InitiateAuth", {
    AuthFlow: "USER_AUTH",
    ClientId: clientId,
    AuthParameters: { USERNAME: email, PREFERRED_CHALLENGE: "EMAIL_OTP" },
  }, ctx);
  if (data?.ChallengeName === "SELECT_CHALLENGE") {
    data = await call("RespondToAuthChallenge", {
      ChallengeName: "SELECT_CHALLENGE",
      ClientId: clientId,
      Session: data.Session,
      ChallengeResponses: { USERNAME: email, ANSWER: "EMAIL_OTP" },
    }, ctx);
  }
  if (data?.ChallengeName !== "EMAIL_OTP" || !data?.Session) throw new CognitoError("UnexpectedChallenge", 502);
  return String(data.Session);
}

export interface Pending {
  email: string;
  mode: Mode;
  session: string | null;
  at?: number; // unix seconds when the code was requested (absent in a pending step sealed before this field)
}

// Step 1: mail a code to `email` (already normalised). Returns what step 2 needs (kept sealed in a cookie).
export async function start(email: string, ctx: CallContext): Promise<Pending> {
  const clientId = ctx.clientId ?? TRAVELLER_CLIENT_ID;
  try {
    // Username = the email (the pool's username attribute); no password, no attributes (the client writes only
    // `name`, and the trigger refuses email_verified).
    const body: Record<string, unknown> = { ClientId: clientId, Username: email };
    if (ctx.signupProofKey) {
      const ts = String(nowS(ctx));
      body.ValidationData = [
        { Name: "bff_ts", Value: ts },
        { Name: "bff_proof", Value: signUpProof(ctx.signupProofKey, email, ts) },
      ];
    }
    const data = await call("SignUp", body, ctx);
    return { email, mode: "signup", session: data?.Session ? String(data.Session) : null, at: nowS(ctx) };
  } catch (e) {
    if (!(e instanceof CognitoError) || e.type !== "UsernameExistsException") throw e;
  }
  try {
    return { email, mode: "signin", session: await requestSignInCode(email, ctx), at: nowS(ctx) };
  } catch (e) {
    if (!(e instanceof CognitoError) || e.type !== "UserNotConfirmedException") throw e;
  }
  // An earlier sign-up that never entered its code: mail a new sign-up code. Such a user can only have been created
  // by this BFF (the pre sign-up trigger requires its proof), so it has no password.
  await call("ResendConfirmationCode", { ClientId: clientId, Username: email }, ctx);
  return { email, mode: "signup", session: null, at: nowS(ctx) };
}

function isType(e: unknown, ...types: string[]): boolean {
  return e instanceof CognitoError && types.includes(e.type);
}

export type VerifyResult = { tokens: Tokens } | { next: Pending };

// Step 2: check the code. Tokens, or (sign-up confirmed but no session to sign in with) a new pending sign-in step:
// a sign-in code has then been mailed.
export async function verify(pending: Pending, code: string, ctx: CallContext): Promise<VerifyResult> {
  const clientId = ctx.clientId ?? TRAVELLER_CLIENT_ID;
  if (pending.mode === "signin") {
    let data;
    try {
      data = await call("RespondToAuthChallenge", {
        ChallengeName: "EMAIL_OTP",
        ClientId: clientId,
        Session: pending.session,
        ChallengeResponses: { USERNAME: pending.email, EMAIL_OTP_CODE: code },
      }, ctx);
    } catch (e) {
      // Not authorised although the session is still young: likely a simulated challenge for an abandoned sign-up.
      const young = typeof pending.at === "number" && nowS(ctx) - pending.at < AUTH_SESSION_S;
      if (!isType(e, "NotAuthorizedException") || !young) throw e;
      try {
        await call("ResendConfirmationCode", { ClientId: clientId, Username: pending.email }, ctx);
      } catch {
        throw e;
      }
      return { next: { email: pending.email, mode: "signup", session: null, at: nowS(ctx) } };
    }
    return { tokens: tokens(data) };
  }
  const confirm = (session: string | null) => call("ConfirmSignUp", {
    ClientId: clientId,
    Username: pending.email,
    ConfirmationCode: code,
    ...(session ? { Session: session } : {}),
  }, ctx);
  let confirmed;
  try {
    confirmed = await confirm(pending.session);
  } catch (e) {
    // A Session older than 3 minutes may be refused while the code itself is still good: retry without it (the
    // traveller then gets a sign-in code below).
    if (!pending.session || !isType(e, "NotAuthorizedException", "ExpiredCodeException")) throw e;
    confirmed = await confirm(null);
  }
  if (confirmed?.Session) {
    const data = await call("InitiateAuth", {
      AuthFlow: "USER_AUTH",
      ClientId: clientId,
      AuthParameters: { USERNAME: pending.email },
      Session: confirmed.Session,
    }, ctx);
    if (data?.AuthenticationResult) return { tokens: tokens(data) };
  }
  return {
    next: { email: pending.email, mode: "signin", session: await requestSignInCode(pending.email, ctx), at: nowS(ctx) },
  };
}

export async function refresh(refreshToken: string, ctx: CallContext): Promise<Tokens> {
  const data = await call("GetTokensFromRefreshToken", {
    ClientId: ctx.clientId ?? TRAVELLER_CLIENT_ID,
    RefreshToken: refreshToken,
  }, ctx);
  return tokens(data);
}

export async function revoke(refreshToken: string, ctx: CallContext): Promise<void> {
  await call("RevokeToken", { ClientId: ctx.clientId ?? TRAVELLER_CLIENT_ID, Token: refreshToken }, ctx);
}
