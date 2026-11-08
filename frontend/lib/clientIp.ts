// AA-1038 R5 hosting (ECS behind CloudFront) — the traveller's own address, for the per-IP AI limits (x-tp-client-ip,
// lib/tripProxy.ts) and the Cognito per-traveller WAF key (lib/cognito.ts). Which header carries it depends on the
// edge in front of the server, so the source is a server env value, never something the request can choose:
//
//   TP_CLIENT_IP_SOURCE unset / "forwarded" (local `next dev` / `next start`, CI; no edge in front): the first
//       x-forwarded-for entry, else x-real-ip. Only for a server no traveller reaches (it was the Vercel mode, where
//       the edge overwrote both headers).
//   TP_CLIENT_IP_SOURCE = "cloudfront" (the ECS task behind CloudFront, AA-CIS-Infra tripplanner_web.tf):
//       CloudFront-Viewer-Address ("<ip>:<port>", IPv6 without brackets). CloudFront sets it from the TCP connection
//       and a viewer cannot set it (forwarded by the origin request policy AllViewerAndCloudFrontHeaders); only
//       CloudFront reaches the task (the ALB host rule also requires the origin-verify header). Without it, the
//       x-forwarded-for entry CLOUDFRONT_XFF_HOPS from the right: CloudFront APPENDS the viewer to whatever
//       x-forwarded-for the viewer sent, and the ALB appends the CloudFront address, so the leftmost entries are the
//       viewer's own words there and are never trusted. Same rule as AA-Partner api/app/ratelimit.py.
//
// No next/* import: `npm test` runs it.

export interface HeaderSource {
  get(name: string): string | null;
}

export type ClientIpSource = "forwarded" | "cloudfront";

export const VIEWER_ADDRESS_HEADER = "cloudfront-viewer-address";
// <viewer>, <CloudFront edge>: the viewer is the second entry from the right once the ALB has appended its peer.
export const CLOUDFRONT_XFF_HOPS = 2;

const IPV4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:]{2,45}$/;

function asIp(value: string): string | null {
  const v = value.trim();
  return IPV4.test(v) || (v.includes(":") && IPV6.test(v)) ? v : null;
}

/** The configured source: only the exact "cloudfront" switches; anything else is the plain "forwarded" mode. */
export function clientIpSource(env: Record<string, string | undefined> = process.env): ClientIpSource {
  return (env.TP_CLIENT_IP_SOURCE ?? "").trim().toLowerCase() === "cloudfront" ? "cloudfront" : "forwarded";
}

/** "<ip>:<port>" -> ip. IPv4 has one colon; IPv6 (no brackets) ends with ":<port>". */
export function viewerAddressIp(value: string | null): string | null {
  const v = (value ?? "").trim();
  const i = v.lastIndexOf(":");
  if (i <= 0 || !/^\d{1,5}$/.test(v.slice(i + 1))) return null;
  return asIp(v.slice(0, i).replace(/^\[|\]$/g, ""));
}

export function clientIp(headers: HeaderSource, source: ClientIpSource = clientIpSource()): string | null {
  if (source === "cloudfront") {
    const viewer = viewerAddressIp(headers.get(VIEWER_ADDRESS_HEADER));
    if (viewer) return viewer;
    const hops = (headers.get("x-forwarded-for") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    return hops.length >= CLOUDFRONT_XFF_HOPS ? asIp(hops[hops.length - CLOUDFRONT_XFF_HOPS]) : null;
  }
  return asIp((headers.get("x-forwarded-for") ?? headers.get("x-real-ip") ?? "").split(",")[0]);
}
