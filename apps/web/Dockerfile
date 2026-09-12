# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .

# Both origins are compiled into browser code. A production image must never
# silently point at localhost or at the hosted SixSentences deployment.
ARG NEXT_PUBLIC_SIX_API_URL
ARG NEXT_PUBLIC_APP_URL
ARG NEXT_PUBLIC_LEGAL_BASE_URL
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=""
ARG NEXT_PUBLIC_BROWSER_CAPTURE_STORE_URL=""
RUN case "$NEXT_PUBLIC_SIX_API_URL" in https://*) ;; *) echo "NEXT_PUBLIC_SIX_API_URL must be an explicit HTTPS URL" >&2; exit 1 ;; esac \
 && case "$NEXT_PUBLIC_APP_URL" in https://*) ;; *) echo "NEXT_PUBLIC_APP_URL must be an explicit HTTPS URL" >&2; exit 1 ;; esac \
 && case "$NEXT_PUBLIC_LEGAL_BASE_URL" in https://*) ;; *) echo "NEXT_PUBLIC_LEGAL_BASE_URL must be an explicit HTTPS URL" >&2; exit 1 ;; esac
ENV NEXT_PUBLIC_SIX_API_URL=$NEXT_PUBLIC_SIX_API_URL \
    NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_LEGAL_BASE_URL=$NEXT_PUBLIC_LEGAL_BASE_URL \
    NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID \
    NEXT_PUBLIC_BROWSER_CAPTURE_STORE_URL=$NEXT_PUBLIC_BROWSER_CAPTURE_STORE_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:3000/login || exit 1
CMD ["node", "server.js"]
