# syntax=docker/dockerfile:1.7
FROM node:26-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:26-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .

# Both origins are compiled into browser code. A production image must never
# silently point at localhost or at the hosted SixSentences deployment.
ARG NEXT_PUBLIC_SIX_API_URL
ARG NEXT_PUBLIC_APP_URL
ARG NEXT_PUBLIC_LEGAL_BASE_URL
ARG SIX_ALLOW_INSECURE_LOCAL_HTTP="false"
ARG NEXT_PUBLIC_TERMS_VERSION
ARG NEXT_PUBLIC_PRIVACY_VERSION
ARG NEXT_PUBLIC_DPA_VERSION
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=""
RUN validate_public_url() { \
      value="$1"; name="$2"; \
      case "$value" in \
        https://*) return 0 ;; \
        http://localhost|http://localhost:*|http://localhost/*|http://127.0.0.1|http://127.0.0.1:*|http://127.0.0.1/*) \
          test "$SIX_ALLOW_INSECURE_LOCAL_HTTP" = "true" && return 0 ;; \
      esac; \
      echo "$name must be HTTPS (or loopback HTTP with the explicit local-build opt-in)" >&2; \
      return 1; \
    }; \
    validate_public_url "$NEXT_PUBLIC_SIX_API_URL" NEXT_PUBLIC_SIX_API_URL \
 && validate_public_url "$NEXT_PUBLIC_APP_URL" NEXT_PUBLIC_APP_URL \
 && validate_public_url "$NEXT_PUBLIC_LEGAL_BASE_URL" NEXT_PUBLIC_LEGAL_BASE_URL \
 && test -n "$NEXT_PUBLIC_TERMS_VERSION" \
 && test -n "$NEXT_PUBLIC_PRIVACY_VERSION" \
 && test -n "$NEXT_PUBLIC_DPA_VERSION"
ENV NEXT_PUBLIC_SIX_API_URL=$NEXT_PUBLIC_SIX_API_URL \
    NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_LEGAL_BASE_URL=$NEXT_PUBLIC_LEGAL_BASE_URL \
    NEXT_PUBLIC_TERMS_VERSION=$NEXT_PUBLIC_TERMS_VERSION \
    NEXT_PUBLIC_PRIVACY_VERSION=$NEXT_PUBLIC_PRIVACY_VERSION \
    NEXT_PUBLIC_DPA_VERSION=$NEXT_PUBLIC_DPA_VERSION \
    NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID
RUN npm run build

FROM node:26-alpine AS runner
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
