# CivicPulse AI — web image.
# Build:  docker build -t civicpulse-web --build-arg API_BASE_URL=http://api:8000 apps/web
# NOTE: Next.js bakes rewrite destinations in at build time, so API_BASE_URL is a build arg.
# STATUS: written but NOT yet built/verified (Docker daemon unavailable during Milestone 1).
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG API_BASE_URL=http://api:8000
ENV API_BASE_URL=${API_BASE_URL} NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node /app/.next ./.next
COPY --from=build --chown=node /app/node_modules ./node_modules
COPY --from=build --chown=node /app/package.json /app/next.config.ts ./
COPY --from=build --chown=node /app/public ./public
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
