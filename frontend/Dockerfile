# ==========================================
# 智悦校园 · Next.js 前端镜像
# ==========================================

FROM node:22-alpine AS deps
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --registry="${NPM_REGISTRY}"

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* 在构建期被内联进产物，必须在这里传入
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
# Prisma 客户端是生成产物，不入库；prisma.config.ts 会解析 DATABASE_URL，
# 但 generate 不连库，占位值即可，真实地址在运行期注入
RUN DATABASE_URL=postgresql://placeholder npx prisma generate
RUN npm run build

# 只用来跑 prisma migrate deploy。复用 deps 层，不必再构建一遍前端。
FROM node:22-alpine AS migrator
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json prisma.config.ts ./
COPY prisma ./prisma
CMD ["npx", "prisma", "migrate", "deploy"]

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
