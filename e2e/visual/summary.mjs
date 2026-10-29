// AA-994 S5b — merges results/report/*.json into results/report.json and prints a Markdown table (page x 4 cells:
// 1440 light / 1440 dark / 390 light / 390 dark) for the job summary and the PR comment. A cell is "ok" or the reasons.
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";

const dir = "results/report";
const rows = existsSync(dir)
  ? readdirSync(dir).filter((f) => f.endsWith(".json")).map((f) => JSON.parse(readFileSync(`${dir}/${f}`, "utf8")))
  : [];
writeFileSync("results/report.json", JSON.stringify(rows, null, 1));

const reasons = (r) => {
  const out = [];
  if (r.status !== (r.page === "not-found" ? 404 : 200)) out.push(`HTTP ${r.status}`);
  if (r.appliedTheme !== r.theme) out.push(`theme ${r.appliedTheme}`);
  if (r.overflow.doc > r.overflow.vw + 1) out.push(`overflow ${r.overflow.doc - r.overflow.vw}px`);
  if (r.consoleErrors.length) out.push(`${r.consoleErrors.length} console error(s)`);
  if (r.failedRequests.length) out.push(`${r.failedRequests.length} failed request(s)`);
  return out.length ? `❌ ${out.join(", ")}` : "ok";
};
const cols = [["1440", "light"], ["1440", "dark"], ["390", "light"], ["390", "dark"]];
const pages = [...new Set(rows.map((r) => r.page))];
const lines = [
  `| Page | ${cols.map(([v, t]) => `${v} ${t}`).join(" | ")} |`,
  `|---|${cols.map(() => "---").join("|")}|`,
  ...pages.map((p) => {
    const cells = cols.map(([v, t]) => {
      const r = rows.find((x) => x.page === p && x.viewport === v && x.theme === t);
      return r ? reasons(r) : "not run";
    });
    return `| ${p} | ${cells.join(" | ")} |`;
  }),
];
const bad = rows.filter((r) => reasons(r) !== "ok").length;
console.log(`${rows.length} captures, ${bad} with findings.\n\n${lines.join("\n")}`);
