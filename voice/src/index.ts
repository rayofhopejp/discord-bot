import "dotenv/config";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { DiscordBot } from "./bot";

const token = process.env.DISCORD_TOKEN;
if (!token) {
  console.error("DISCORD_TOKEN is required");
  process.exit(1);
}

// Load serif.txt for character lines
const serifPath = resolve(__dirname, "../serif.txt");
let serifContent = "";
if (existsSync(serifPath)) {
  serifContent = readFileSync(serifPath, "utf-8").trim();
  console.log(`Loaded serif.txt (${serifContent.length} chars)`);
} else {
  console.warn("serif.txt not found - using default system prompt");
}

const systemPrompt = serifContent
  ? `以下の「うさねこらーじ」のセリフを参考にして、うさねこらーじになりきって返答してください。ただし、1-2行に収まるくらい短く返すこと。\n\n${serifContent}`
  : process.env.NOVA_SONIC_SYSTEM_PROMPT;

// Only pass explicit credentials if set; otherwise SDK uses default chain (IAM role, etc.)
const awsCredentials = process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY
  ? {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
      sessionToken: process.env.AWS_SESSION_TOKEN,
    }
  : undefined;

new DiscordBot({
  token,
  awsRegion: process.env.AWS_REGION || "us-east-1",
  awsCredentials,
  voiceId: process.env.NOVA_SONIC_VOICE_ID,
  systemPrompt,
});
