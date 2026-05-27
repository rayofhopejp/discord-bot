import "dotenv/config";
import { DiscordBot } from "./bot";

const token = process.env.DISCORD_TOKEN;
if (!token) {
  console.error("DISCORD_TOKEN is required");
  process.exit(1);
}

new DiscordBot({
  token,
  awsRegion: process.env.AWS_REGION || "us-east-1",
  awsCredentials: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID || "",
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY || "",
    sessionToken: process.env.AWS_SESSION_TOKEN,
  },
  voiceId: process.env.NOVA_SONIC_VOICE_ID,
  systemPrompt: process.env.NOVA_SONIC_SYSTEM_PROMPT,
});
