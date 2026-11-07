import { NextRequest } from "next/server";
import { GET as browseGet } from "../route";

// AA-1015 (S262): free-text search under its own pathname, so the BotID client (which matches the pathname, not the
// query) attaches its headers to search only and map tiles never wait for the challenge (lib/botCheck.ts). The
// answer is the search branch of /api/browse, bot check included.
// AA-1038: the pathname stays /api/browse/search (the browse handler reads only the query), so the edge gate
// (lib/edgeGate.ts) can tell this protected path from the older /api/browse?resource=search spelling.
export async function GET(req: NextRequest) {
  const url = req.nextUrl.clone();
  url.searchParams.set("resource", "search");
  return browseGet(new NextRequest(url, { headers: req.headers, method: "GET" }));
}
