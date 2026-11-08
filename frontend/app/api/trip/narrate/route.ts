import { NextRequest } from "next/server";
import { POST as tripPost } from "../route";

// AA-1015 (S262): narration under its own pathname, so the bot check (the web ACL's rules, AA-1038) matches narration
// only and trip edits are never challenged (lib/botCheck.ts). Same handler as POST /api/trip (body
// `{ op: "narrate", ... }`); proxy.ts matches this path too (guest cookie).
export async function POST(req: NextRequest) {
  return tripPost(req);
}
