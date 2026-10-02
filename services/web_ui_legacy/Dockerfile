FROM nginx:1.27-alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY services/web_ui/nginx.conf /etc/nginx/conf.d/default.conf

# Copy static files
COPY services/web_ui/index.html /usr/share/nginx/html/
COPY services/web_ui/css/ /usr/share/nginx/html/css/
COPY services/web_ui/js/ /usr/share/nginx/html/js/

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
