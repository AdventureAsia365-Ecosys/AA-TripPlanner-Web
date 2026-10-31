// AA-1015 (S262) — BotID client (Basic level, free): attaches the challenge headers to the two calls that can reach a
// model, GET /api/browse/search and POST /api/trip/narrate (lib/botCheck.ts). Runs only when the build has the check
// on (a Vercel build by default); locally the challenge script does not exist, so the client stays off there.
import { initBotId } from "botid/client/core";
import { BOT_PROTECT, botCheckOn } from "./lib/botCheck";

if (botCheckOn()) initBotId({ protect: BOT_PROTECT });
