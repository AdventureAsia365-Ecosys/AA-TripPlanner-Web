// AA-908: the BFF's passwordless Cognito flow (lib/cognito.ts) against a fake Cognito. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AUTH_SESSION_S,
  BFF_KEY_HEADER,
  COGNITO_ENDPOINT,
  CognitoError,
  TRAVELLER_CLIENT_ID,
  TRAVELLER_IP_HEADER,
  errorCode,
  normaliseEmail,
  refresh,
  revoke,
  signUpProof,
  start,
  travellerIp,
  verify,
  type CallContext,
} from "./cognito.ts";

interface Seen {
  action: string;
  body: any;
  headers: Record<string, string>;
}

// A fake Cognito: answers per action from `script` (a body, or an exception name with a status).
function fakeCognito(script: Record<string, Array<{ body?: unknown; error?: string; status?: number }>>) {
  const seen: Seen[] = [];
  const fetchImpl = (async (url: string, init: any) => {
    assert.equal(url, COGNITO_ENDPOINT);
    const action = String(init.headers["x-amz-target"]).replace("AWSCognitoIdentityProviderService.", "");
    seen.push({ action, body: JSON.parse(init.body), headers: init.headers });
    const next = script[action]?.shift();
    if (!next) throw new Error(`unexpected ${action}`);
    if (next.error !== undefined) {
      return new Response(JSON.stringify(next.error ? { __type: next.error, message: "secret detail" } : {}),
        { status: next.status ?? 400 });
    }
    return new Response(JSON.stringify(next.body ?? {}), { status: 200 });
  }) as unknown as typeof fetch;
  const ctx: CallContext = { bffKey: "bff-key", signupProofKey: PROOF_KEY, travellerIp: "203.0.113.7", fetchImpl,
    now: () => NOW };
  return { seen, ctx };
}

const NOW = 1_800_000_000;
const PROOF_KEY = "p".repeat(48);

const AUTH = { AuthenticationResult: { AccessToken: "acc", RefreshToken: "ref", ExpiresIn: 3600 } };

test("a new address signs up without a password, and the code signs in with the confirm session", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ body: { UserConfirmed: false, Session: "s-up" } }],
    ConfirmSignUp: [{ body: { Session: "s-confirmed" } }],
    InitiateAuth: [{ body: AUTH }],
  });
  const pending = await start("new@example.com", ctx);
  assert.deepEqual(pending, { email: "new@example.com", mode: "signup", session: "s-up", at: NOW });
  const r = await verify(pending, "123456", ctx);
  assert.deepEqual(r, { tokens: { accessToken: "acc", refreshToken: "ref", expiresIn: 3600 } });
  assert.deepEqual(seen[0].body, { ClientId: TRAVELLER_CLIENT_ID, Username: "new@example.com", ValidationData: [
    { Name: "bff_ts", Value: String(NOW) },
    { Name: "bff_proof", Value: signUpProof(PROOF_KEY, "new@example.com", String(NOW)) },
  ] });
  assert.equal("Password" in seen[0].body, false);
  assert.deepEqual(seen[1].body, { ClientId: TRAVELLER_CLIENT_ID, Username: "new@example.com",
    ConfirmationCode: "123456", Session: "s-up" });
  assert.deepEqual(seen[2].body, { AuthFlow: "USER_AUTH", ClientId: TRAVELLER_CLIENT_ID,
    AuthParameters: { USERNAME: "new@example.com" }, Session: "s-confirmed" });
});

test("an existing address gets a sign-in code (EMAIL_OTP) and the code returns tokens", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ error: "UsernameExistsException" }],
    InitiateAuth: [{ body: { ChallengeName: "EMAIL_OTP", Session: "s-otp" } }],
    RespondToAuthChallenge: [{ body: AUTH }],
  });
  const pending = await start("old@example.com", ctx);
  assert.deepEqual(pending, { email: "old@example.com", mode: "signin", session: "s-otp", at: NOW });
  assert.deepEqual(seen[1].body.AuthParameters, { USERNAME: "old@example.com", PREFERRED_CHALLENGE: "EMAIL_OTP" });
  const r = await verify(pending, "654321", ctx);
  assert.ok("tokens" in r);
  assert.deepEqual(seen[2].body, { ChallengeName: "EMAIL_OTP", ClientId: TRAVELLER_CLIENT_ID, Session: "s-otp",
    ChallengeResponses: { USERNAME: "old@example.com", EMAIL_OTP_CODE: "654321" } });
});

test("a SELECT_CHALLENGE answer is answered with EMAIL_OTP", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ error: "UsernameExistsException" }],
    InitiateAuth: [{ body: { ChallengeName: "SELECT_CHALLENGE", Session: "s1" } }],
    RespondToAuthChallenge: [{ body: { ChallengeName: "EMAIL_OTP", Session: "s2" } }],
  });
  assert.deepEqual(await start("old@example.com", ctx),
    { email: "old@example.com", mode: "signin", session: "s2", at: NOW });
  assert.deepEqual(seen[2].body.ChallengeResponses, { USERNAME: "old@example.com", ANSWER: "EMAIL_OTP" });
});

test("an unconfirmed earlier sign-up gets a new sign-up code", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ error: "UsernameExistsException" }],
    InitiateAuth: [{ error: "UserNotConfirmedException" }],
    ResendConfirmationCode: [{ body: {} }],
  });
  assert.deepEqual(await start("half@example.com", ctx),
    { email: "half@example.com", mode: "signup", session: null, at: NOW });
  assert.deepEqual(seen.map((s) => s.action), ["SignUp", "InitiateAuth", "ResendConfirmationCode"]);
});

test("a confirmed sign-up without a session mails a sign-in code instead", async () => {
  const { ctx } = fakeCognito({
    ConfirmSignUp: [{ body: {} }],
    InitiateAuth: [{ body: { ChallengeName: "EMAIL_OTP", Session: "s-otp" } }],
  });
  const r = await verify({ email: "half@example.com", mode: "signup", session: null }, "111111", ctx);
  assert.deepEqual(r, { next: { email: "half@example.com", mode: "signin", session: "s-otp", at: NOW } });
});

test("every call carries the BFF key and the traveller address for the web ACL", async () => {
  const { seen, ctx } = fakeCognito({ SignUp: [{ body: {} }] });
  await start("a@example.com", ctx);
  assert.equal(seen[0].headers[BFF_KEY_HEADER], "bff-key");
  assert.equal(seen[0].headers[TRAVELLER_IP_HEADER], "203.0.113.7");
  assert.equal(seen[0].headers["content-type"], "application/x-amz-json-1.1");
});

test("refresh keeps a rotated refresh token; revoke sends the refresh token", async () => {
  const { seen, ctx } = fakeCognito({
    GetTokensFromRefreshToken: [{ body: { AuthenticationResult: { AccessToken: "acc2", ExpiresIn: 3600 } } }],
    RevokeToken: [{ body: {} }],
  });
  assert.deepEqual(await refresh("rt", ctx), { accessToken: "acc2", refreshToken: null, expiresIn: 3600 });
  await revoke("rt", ctx);
  assert.deepEqual(seen[1].body, { ClientId: TRAVELLER_CLIENT_ID, Token: "rt" });
});

test("errors map to codes that never carry Cognito's message", async () => {
  const cases: Array<[string, number, string]> = [
    ["InvalidParameterException", 400, "invalid_email"],
    ["CodeMismatchException", 400, "code_invalid"],
    ["ExpiredCodeException", 400, "code_expired"],
    ["NotAuthorizedException", 400, "code_expired"],
    ["TooManyRequestsException", 400, "try_later"],
    ["LimitExceededException", 400, "try_later"],
    ["UserLambdaValidationException", 400, "sign_up_unavailable"],
    ["CodeDeliveryFailureException", 400, "delivery_failed"],
    ["", 403, "try_later"],                       // a WAF block
    ["InternalErrorException", 500, "unavailable"],
  ];
  for (const [type, status, code] of cases) {
    const { ctx } = fakeCognito({ SignUp: [{ error: type, status }] });
    await assert.rejects(start("a@example.com", ctx), (e: unknown) => {
      assert.ok(e instanceof CognitoError);
      assert.equal(errorCode(e), code);
      assert.equal(String(e).includes("secret detail"), false);
      return true;
    });
  }
  assert.equal(errorCode(new Error("x")), "unavailable");
  const prefixed = fakeCognito({ SignUp: [{ error: "com.amazonaws#CodeMismatchException", status: 400 }] });
  await assert.rejects(start("a@example.com", prefixed.ctx), (e: unknown) => errorCode(e) === "code_invalid");
});

test("emails are normalised and checked", () => {
  assert.equal(normaliseEmail("  Jo@Example.COM "), "jo@example.com");
  for (const bad of ["", "jo", "jo@x", "a b@c.de", null, 3, `${"a".repeat(250)}@x.co`]) {
    assert.equal(normaliseEmail(bad), null);
  }
});

test("the traveller address is the first x-forwarded-for entry when it is an IP", () => {
  const h = (v: Record<string, string>) => ({ get: (k: string) => v[k] ?? null });
  assert.equal(travellerIp(h({ "x-forwarded-for": "198.51.100.4, 10.0.0.1" })), "198.51.100.4");
  assert.equal(travellerIp(h({ "x-real-ip": "2001:db8::1" })), "2001:db8::1");
  assert.equal(travellerIp(h({ "x-forwarded-for": "not-an-ip" })), null);
  assert.equal(travellerIp(h({})), null);
});

// ---------------------------------------------------------------- review round (S250)
test("the sign-up proof matches the pre sign-up trigger's vector (AA-CIS-Infra test_external.py)", () => {
  assert.equal(signUpProof("k".repeat(40), "traveller@example.com", "1800000000"),
    "0df8bd490e0f009967f1349b1843abf6b1110bec7c7fb9a4a2f1f718006fdfda");
});

test("without a proof key, SignUp carries no ValidationData (the trigger then refuses a new address)", async () => {
  const { seen, ctx } = fakeCognito({ SignUp: [{ body: {} }] });
  await start("a@example.com", { ...ctx, signupProofKey: "" });
  assert.equal("ValidationData" in seen[0].body, false);
});

test("without a traveller address, neither WAF header is sent (the call stays on the per-IP rules)", async () => {
  const { seen, ctx } = fakeCognito({ SignUp: [{ body: {} }] });
  await start("a@example.com", { ...ctx, travellerIp: null });
  assert.equal(BFF_KEY_HEADER in seen[0].headers, false);
  assert.equal(TRAVELLER_IP_HEADER in seen[0].headers, false);
});

test("a stale sign-up Session is retried without one, then a sign-in code is mailed", async () => {
  const { seen, ctx } = fakeCognito({
    ConfirmSignUp: [{ error: "NotAuthorizedException" }, { body: {} }],
    InitiateAuth: [{ body: { ChallengeName: "EMAIL_OTP", Session: "s-otp" } }],
  });
  const r = await verify({ email: "new@example.com", mode: "signup", session: "s-old", at: NOW - 400 }, "123456", ctx);
  assert.deepEqual(r, { next: { email: "new@example.com", mode: "signin", session: "s-otp", at: NOW } });
  assert.equal(seen[0].body.Session, "s-old");
  assert.equal("Session" in seen[1].body, false);
});

test("a wrong sign-up code is not retried", async () => {
  const { seen, ctx } = fakeCognito({ ConfirmSignUp: [{ error: "CodeMismatchException" }] });
  await assert.rejects(verify({ email: "n@example.com", mode: "signup", session: "s", at: NOW }, "1", ctx),
    (e: unknown) => errorCode(e) === "code_invalid");
  assert.equal(seen.length, 1);
});

test("a young sign-in refused as not authorised mails a new sign-up code (abandoned sign-up)", async () => {
  const { seen, ctx } = fakeCognito({
    RespondToAuthChallenge: [{ error: "NotAuthorizedException" }],
    ResendConfirmationCode: [{ body: {} }],
  });
  const r = await verify({ email: "half@example.com", mode: "signin", session: "s", at: NOW - 30 }, "123456", ctx);
  assert.deepEqual(r, { next: { email: "half@example.com", mode: "signup", session: null, at: NOW } });
  assert.deepEqual(seen.map((s) => s.action), ["RespondToAuthChallenge", "ResendConfirmationCode"]);
});

test("an old sign-in session refused as not authorised is simply expired", async () => {
  for (const at of [NOW - AUTH_SESSION_S, undefined]) {
    const { seen, ctx } = fakeCognito({ RespondToAuthChallenge: [{ error: "NotAuthorizedException" }] });
    await assert.rejects(verify({ email: "o@example.com", mode: "signin", session: "s", at }, "123456", ctx),
      (e: unknown) => errorCode(e) === "code_expired");
    assert.equal(seen.length, 1);
  }
});

test("when the new sign-up code cannot be sent, the original refusal stands", async () => {
  const { ctx } = fakeCognito({
    RespondToAuthChallenge: [{ error: "NotAuthorizedException" }],
    ResendConfirmationCode: [{ error: "InvalidParameterException" }],
  });
  await assert.rejects(verify({ email: "o@example.com", mode: "signin", session: "s", at: NOW - 10 }, "123456", ctx),
    (e: unknown) => errorCode(e) === "code_expired");
});
