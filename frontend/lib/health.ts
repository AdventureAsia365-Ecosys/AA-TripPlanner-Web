// AA-891: the body of GET /api/health — liveness of the web app plus what it was built from.
// APP_VERSION (the release-please manifest version) and GIT_SHA are baked into the image (frontend/Dockerfile build
// args, .github/workflows/deploy-web-dev.yml), so a rolled-back task keeps reporting its own build (AA-1038: the site
// runs on ECS; the Vercel deploy and its VERCEL_GIT_COMMIT_SHA fallback are gone). This route is the target group and
// container health check, so it never calls upstream.
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
    git_sha: env.GIT_SHA?.trim() || "unknown",
  };
}
