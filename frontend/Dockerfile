# syntax=docker/dockerfile:1.7
FROM node:24-alpine AS build
WORKDIR /src
ENV NX_DAEMON=false NX_NO_CLOUD=true CI=true
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npx nx build web --configuration=production

# nginx không chạy bằng root, nghe cổng 8080
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist/apps/web/browser /usr/share/nginx/html
EXPOSE 8080
