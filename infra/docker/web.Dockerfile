FROM node:24.12.0-bookworm-slim

ENV COREPACK_HOME=/tmp/corepack \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY apps/web/package.json apps/web/package.json
COPY apps/sandbox/package.json apps/sandbox/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN corepack pnpm@10.34.4 install --frozen-lockfile

COPY apps/web apps/web
COPY packages/contracts packages/contracts
COPY packages/ui packages/ui
RUN corepack pnpm@10.34.4 --filter @trust/web build

EXPOSE 3000
CMD ["corepack", "pnpm@10.34.4", "--filter", "@trust/web", "start"]
