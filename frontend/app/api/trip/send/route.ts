import { NextRequest } from "next/server";
import { POST as tripPost } from "../route";

// AA-1038 slice 3 (S272 review): "Send to advisor" under its own pathname, so the bot check (the web ACL's rules)
// matches this call only and trip edits are never challenged (lib/botCheck.ts). Same handler as POST /api/trip (body
// `{ op: "send", ... }`); proxy.ts matches this path too (guest cookie).
export async function POST(req: NextRequest) {
  return tripPost(req);
}
