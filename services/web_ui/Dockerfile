# Multi-stage Docker build for React + Vite Web UI
FROM node:22-alpine AS build

WORKDIR /app

# Copy dependency manifests first for cached layer
COPY services/web_ui/package*.json ./
RUN npm ci

# Copy source and build production bundle
COPY services/web_ui/ ./
RUN npm run build

# Stage 2: Production Nginx Server
FROM nginx:1.27-alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY services/web_ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
