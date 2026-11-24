FROM node:26-alpine@sha256:e88a35be04478413b7c71c455cd9865de9b9360e1f43456be5951032d7ac1a66 AS build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig*.json vite.config.ts ./
COPY scripts/copy-flags.mjs ./scripts/
COPY public ./public
COPY src ./src
RUN npm run build

FROM nginx:1.31.10-alpine@sha256:aceb952b920393b7d0bc7b978927fc283370bea89fb082c6fef0ce18c60f96a9
RUN apk add --no-cache libcrypto3=3.5.9-r1 libssl3=3.5.9-r1 zlib=1.4-r0

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 80
