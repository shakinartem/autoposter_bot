FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S autoposter && adduser -S autoposter -G autoposter
COPY --from=builder --chown=autoposter:autoposter /app/package.json ./package.json
COPY --from=builder --chown=autoposter:autoposter /app/node_modules ./node_modules
COPY --from=builder --chown=autoposter:autoposter /app/.next ./.next
COPY --from=builder --chown=autoposter:autoposter /app/public ./public
USER autoposter
EXPOSE 3000
CMD ["npm", "run", "start"]
