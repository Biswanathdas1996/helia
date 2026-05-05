FROM node:24-alpine AS build
WORKDIR /repo
RUN corepack enable
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json tsconfig.base.json tsconfig.json ./
COPY artifacts/support-ai ./artifacts/support-ai
COPY lib ./lib
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @workspace/support-ai run build

FROM nginx:1.27-alpine
COPY --from=build /repo/artifacts/support-ai/dist /usr/share/nginx/html
COPY artifacts/support-ai/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
