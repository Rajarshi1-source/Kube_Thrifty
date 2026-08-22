# ================================================================================================
# The dashboard image.
#
# Three stages, so the runtime layer contains no build toolchain and no dev dependencies. The result
# is small because `output: 'standalone'` in next.config.ts makes Next trace the actual module graph
# and emit only what the server needs -- rather than shipping the whole node_modules tree.
# ================================================================================================
FROM node:24.12.0-alpine3.22 AS deps

WORKDIR /app

# Only the manifests, so this layer caches until a dependency actually changes.
COPY package.json package-lock.json ./
# `npm ci` honours the lockfile exactly and fails if package.json and the lock disagree -- which is
# what makes the built image match what CI tested.
RUN npm ci --ignore-scripts

# ------------------------------------------------------------------------------------------------
FROM node:24.12.0-alpine3.22 AS build

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `public/` is optional in Next.js and this app does not currently ship one; ensure it exists so the
# runtime stage's COPY below doesn't fail on a directory that was never committed.
RUN mkdir -p public

# lib/env.ts validates configuration at MODULE LOAD, so the build would fail without these. They are
# build-time placeholders only: the real values are injected at runtime from a Secret, and nothing
# here is baked into the output.
ENV DATABASE_URL=postgresql://build:build@localhost:5432/build \
    REDIS_URL=redis://localhost:6379 \
    PROMETHEUS_URL=http://localhost:9090 \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ------------------------------------------------------------------------------------------------
FROM node:24.12.0-alpine3.22

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 65532 is the conventional `nonroot` uid, matching the securityContext in the Helm chart. Created
# explicitly because the node alpine images define `node` (1000) instead.
RUN addgroup -g 65532 -S nonroot \
 && adduser -u 65532 -S nonroot -G nonroot

# The standalone output already contains the traced subset of node_modules.
COPY --from=build --chown=65532:65532 /app/.next/standalone ./
COPY --from=build --chown=65532:65532 /app/.next/static ./.next/static
COPY --from=build --chown=65532:65532 /app/public ./public

# Writable, because the chart mounts the root filesystem read-only. Next writes its runtime image
# and fetch caches here.
RUN mkdir -p /app/.next/cache && chown -R 65532:65532 /app/.next/cache

USER 65532:65532

EXPOSE 3000

# Liveness is dependency-FREE on purpose: if it checked the database, a dependency outage would
# restart this pod, which cannot fix the dependency and converts one outage into a crash-loop.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://localhost:3000/api/health?probe=live').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# The standalone server. NOT `npm start`, which would need the full next CLI in the runtime layer.
CMD ["node", "server.js"]
