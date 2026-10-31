import { NextRequest } from "next/server";
import { POST as tripPost } from "../route";

// AA-1015 (S262): narration under its own pathname, so the BotID client (which matches the pathname) attaches its
// headers to narration only and trip edits never wait for the challenge (lib/botCheck.ts). Same handler as
// POST /api/trip (body `{ op: "narrate", ... }`), bot check included; proxy.ts matches this path too (guest cookie).
export async function POST(req: NextRequest) {
  return tripPost(req);
}
