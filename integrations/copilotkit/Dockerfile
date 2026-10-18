# The example app as a container (Next.js standalone). Configuration comes from the environment at run time:
#   docker run -p 3000:3000 -e HANGAR_URL=... -e HANGAR_AGENT=... -e HANGAR_KEY=... agent-hangar/copilotkit-example
ARG NODE_ALPINE_IMAGE=node:22-alpine
FROM ${NODE_ALPINE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 COPILOTKIT_TELEMETRY_DISABLED=true
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx next build

FROM ${NODE_ALPINE_IMAGE}
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 COPILOTKIT_TELEMETRY_DISABLED=true PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
