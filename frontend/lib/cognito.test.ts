// AA-908: the BFF's passwordless Cognito flow (lib/cognito.ts) against a fake Cognito. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  BFF_KEY_HEADER,
  COGNITO_ENDPOINT,
  CognitoError,
  TRAVELLER_CLIENT_ID,
  TRAVELLER_IP_HEADER,
  errorCode,
  normaliseEmail,
  refresh,
  revoke,
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
  const ctx: CallContext = { bffKey: "bff-key", travellerIp: "203.0.113.7", fetchImpl };
  return { seen, ctx };
}

const AUTH = { AuthenticationResult: { AccessToken: "acc", RefreshToken: "ref", ExpiresIn: 3600 } };

test("a new address signs up without a password, and the code signs in with the confirm session", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ body: { UserConfirmed: false, Session: "s-up" } }],
    ConfirmSignUp: [{ body: { Session: "s-confirmed" } }],
    InitiateAuth: [{ body: AUTH }],
  });
  const pending = await start("new@example.com", ctx);
  assert.deepEqual(pending, { email: "new@example.com", mode: "signup", session: "s-up" });
  const r = await verify(pending, "123456", ctx);
  assert.deepEqual(r, { tokens: { accessToken: "acc", refreshToken: "ref", expiresIn: 3600 } });
  assert.deepEqual(seen[0].body, { ClientId: TRAVELLER_CLIENT_ID, Username: "new@example.com" });
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
  assert.deepEqual(pending, { email: "old@example.com", mode: "signin", session: "s-otp" });
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
  assert.deepEqual(await start("old@example.com", ctx), { email: "old@example.com", mode: "signin", session: "s2" });
  assert.deepEqual(seen[2].body.ChallengeResponses, { USERNAME: "old@example.com", ANSWER: "EMAIL_OTP" });
});

test("an unconfirmed earlier sign-up gets a new sign-up code", async () => {
  const { seen, ctx } = fakeCognito({
    SignUp: [{ error: "UsernameExistsException" }],
    InitiateAuth: [{ error: "UserNotConfirmedException" }],
    ResendConfirmationCode: [{ body: {} }],
  });
  assert.deepEqual(await start("half@example.com", ctx), { email: "half@example.com", mode: "signup", session: null });
  assert.deepEqual(seen.map((s) => s.action), ["SignUp", "InitiateAuth", "ResendConfirmationCode"]);
});

test("a confirmed sign-up without a session mails a sign-in code instead", async () => {
  const { ctx } = fakeCognito({
    ConfirmSignUp: [{ body: {} }],
    InitiateAuth: [{ body: { ChallengeName: "EMAIL_OTP", Session: "s-otp" } }],
  });
  const r = await verify({ email: "half@example.com", mode: "signup", session: null }, "111111", ctx);
  assert.deepEqual(r, { next: { email: "half@example.com", mode: "signin", session: "s-otp" } });
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
