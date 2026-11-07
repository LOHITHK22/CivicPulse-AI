# CivicPulse AI — web image.
# Build:  docker build -t civicpulse-web apps/web
# Runtime env (server-side only, read by the app/api route handlers): API_BASE_URL, CIVICPULSE_PROXY_SECRET,
# CIVICPULSE_AI_ACCESS_TOKEN, CIVICPULSE_CLIENT_IP_HEADER. See docs/deployment/ENVIRONMENT_VARIABLES.md.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 API_BASE_URL=http://api:8000
COPY --from=build --chown=node /app/.next ./.next
COPY --from=build --chown=node /app/node_modules ./node_modules
COPY --from=build --chown=node /app/package.json /app/next.config.ts ./
COPY --from=build --chown=node /app/public ./public
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
