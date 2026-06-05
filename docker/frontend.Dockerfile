FROM node:22-alpine

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./

EXPOSE 5174

CMD ["npm", "run", "dev:docker"]
