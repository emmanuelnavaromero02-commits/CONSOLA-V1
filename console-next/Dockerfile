# v1.44.2 — Next.js 14 standalone build, multi-stage.
#
# Stage 1 (deps):   resolves package-lock.json — cached unless deps change.
# Stage 2 (build):  next build with output:"standalone" → minimal server.
# Stage 3 (runner): node:20-alpine + just the standalone bundle. No
#                   node_modules in the final image.
#
# Final image size target: ~150 MB.

FROM node:20-alpine AS deps
WORKDIR /app
# Only the lockfile + manifest land in the deps layer; the source tree
# arrives in the build stage. That way a code-only change re-runs only
# the build stage, not npm ci.
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Non-root runtime — defence-in-depth, matches the v1.43+ posture for
# the Python services.
RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1:3000/api/health || exit 1

CMD ["node", "server.js"]
