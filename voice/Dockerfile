FROM node:22-slim

RUN apt-get update && apt-get install -y \
    python3 make g++ libopus-dev libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --production=false

COPY tsconfig.json ./
COPY src/ ./src/
COPY serif.txt ./

RUN npm run build
RUN npm prune --production

CMD ["node", "dist/index.js"]
