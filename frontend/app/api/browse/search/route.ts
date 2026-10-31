import { NextRequest } from "next/server";
import { GET as browseGet } from "../route";

// AA-1015 (S262): free-text search under its own pathname, so the BotID client (which matches the pathname, not the
// query) attaches its headers to search only and map tiles never wait for the challenge (lib/botCheck.ts). The
// answer is the search branch of /api/browse, bot check included.
export async function GET(req: NextRequest) {
  const url = req.nextUrl.clone();
  url.pathname = "/api/browse";
  url.searchParams.set("resource", "search");
  return browseGet(new NextRequest(url, { headers: req.headers, method: "GET" }));
}
