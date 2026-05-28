# Multi-stage build. Tests run in the `build` stage — failure there blocks
# the image, so a container can never start with broken unit tests.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --include=dev

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build
# Gate: if any unit test fails, the build fails, so the runtime stage never
# starts. No need to re-run tests in CI for a smoke check — the image itself
# is proof the tests passed.
RUN npm test

FROM node:20-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
USER app
EXPOSE 3001
CMD ["node", "dist/main.js"]
