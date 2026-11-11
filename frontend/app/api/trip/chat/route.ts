import { NextRequest } from "next/server";
import { POST as tripPost } from "../route";

// AA-1041 slice 2: the planning chat under its own pathname, so the web ACL's rate rule and Challenge (the bot check of
// the protected paths, lib/botCheck.ts) cover every chat message and nothing else. Same handler as POST /api/trip
// (body `{ op: "chat", ... }`); with the edge gate on, the chat op is answered only here (lib/edgeGate.ts). proxy.ts
// matches this path too (guest cookie).
export async function POST(req: NextRequest) {
  return tripPost(req);
}
