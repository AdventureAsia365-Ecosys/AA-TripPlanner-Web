// AA-891: the body of GET /api/health — liveness of the web app plus what it was built from.
// APP_VERSION (the release-please manifest version) and GIT_SHA are set per deployment by
// .github/workflows/vercel-deploy.yml (`vercel deploy --env`), so a promoted or rolled-back deployment
// keeps reporting its own build. VERCEL_GIT_COMMIT_SHA is the fallback for a deployment made another way.
export type HealthBody = {
  status: "ok";
  service: string;
  version: string;
  git_sha: string;
};

export type HealthEnv = Record<string, string | undefined>;

export function healthBody(env: HealthEnv): HealthBody {
  return {
    status: "ok",
    service: "aa-tripplanner-web",
    version: env.APP_VERSION?.trim() || "unknown",
    git_sha: env.GIT_SHA?.trim() || env.VERCEL_GIT_COMMIT_SHA?.trim() || "unknown",
  };
}
