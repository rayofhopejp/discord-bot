import {
  Client,
  GatewayIntentBits,
  VoiceState,
  ChannelType,
  Message,
} from "discord.js";
import {
  joinVoiceChannel,
  VoiceConnection,
  VoiceConnectionStatus,
  entersState,
  createAudioPlayer,
  createAudioResource,
  AudioPlayerStatus,
  StreamType,
  getVoiceConnection,
  EndBehaviorType,
} from "@discordjs/voice";
import { OpusEncoder } from "@discordjs/opus";
import { Transform, Readable, PassThrough } from "node:stream";
import { Buffer } from "node:buffer";
import { NovaSonicSession } from "./nova-sonic";

// Discord sends 48kHz stereo Opus, we need 16kHz mono PCM for Nova Sonic
// Nova Sonic returns 24kHz mono PCM, Discord needs 48kHz stereo Opus

interface UserSession {
  novaSonic: NovaSonicSession;
  outputBuffer: Buffer[];
  isPlaying: boolean;
}

export class DiscordBot {
  private client: Client;
  private sessions = new Map<string, UserSession>(); // guildId -> session
  private awsRegion: string;
  private awsCredentials?: { accessKeyId: string; secretAccessKey: string; sessionToken?: string };
  private voiceId: string;
  private systemPrompt: string;

  constructor(config: {
    token: string;
    awsRegion: string;
    awsCredentials?: { accessKeyId: string; secretAccessKey: string; sessionToken?: string };
    voiceId?: string;
    systemPrompt?: string;
  }) {
    this.awsRegion = config.awsRegion;
    this.awsCredentials = config.awsCredentials;
    this.voiceId = config.voiceId || "tiffany";
    this.systemPrompt = config.systemPrompt || "You are a friendly assistant in a Discord voice channel. Keep responses short and conversational, two or three sentences max.";

    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildVoiceStates,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
      ],
    });

    this.client.on("ready", () => {
      console.log(`Bot logged in as ${this.client.user?.tag}`);
    });

    this.client.on("messageCreate", (msg) => this.handleMessage(msg));

    this.client.login(config.token);
  }

  private async handleMessage(msg: Message): Promise<void> {
    if (msg.author.bot) return;

    if (msg.content === "!join") {
      const voiceChannel = msg.member?.voice.channel;
      if (!voiceChannel || voiceChannel.type !== ChannelType.GuildVoice) {
        await msg.reply("ボイスチャンネルに入ってから `!join` してください。");
        return;
      }

      const connection = joinVoiceChannel({
        channelId: voiceChannel.id,
        guildId: voiceChannel.guild.id,
        adapterCreator: voiceChannel.guild.voiceAdapterCreator,
        selfDeaf: false,
      });

      await this.setupConnection(connection, voiceChannel.guild.id);
      await msg.reply(`🎙️ ${voiceChannel.name} に参加しました！話しかけてください。`);
    }

    if (msg.content === "!leave") {
      const guildId = msg.guildId;
      if (!guildId) return;
      await this.disconnect(guildId);
      await msg.reply("👋 退出しました。");
    }
  }

  private async setupConnection(connection: VoiceConnection, guildId: string): Promise<void> {
    // Wait for connection to be ready
    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
    } catch {
      connection.destroy();
      return;
    }

    // Handle disconnects
    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      try {
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
        ]);
      } catch {
        await this.disconnect(guildId);
      }
    });

    // Create Nova Sonic session
    const novaSonic = new NovaSonicSession({
      region: this.awsRegion,
      credentials: this.awsCredentials,
      voiceId: this.voiceId,
      systemPrompt: this.systemPrompt,
    });

    const session: UserSession = {
      novaSonic,
      outputBuffer: [],
      isPlaying: false,
    };

    this.sessions.set(guildId, session);

    // Handle audio output from Nova Sonic
    const player = createAudioPlayer();
    connection.subscribe(player);

    novaSonic.onAudioOutput = (audioBase64: string) => {
      // Nova Sonic outputs 24kHz 16-bit mono PCM
      const pcm24k = Buffer.from(audioBase64, "base64");
      // Resample 24kHz mono -> 48kHz stereo for Discord
      const pcm48kStereo = resample24kMonoTo48kStereo(pcm24k);
      session.outputBuffer.push(pcm48kStereo);

      if (!session.isPlaying) {
        this.playBuffered(session, player);
      }
    };

    novaSonic.onTextOutput = (text: string, role: string) => {
      console.log(`[${role}] ${text}`);
    };

    novaSonic.onError = (err) => {
      console.error("Nova Sonic error:", err);
      // Don't destroy the connection on Nova Sonic errors
    };

    // Start Nova Sonic session
    try {
      await novaSonic.start();
    } catch (err) {
      console.error("Failed to start Nova Sonic session:", err);
      // Keep the bot in the channel even if Nova Sonic fails
      return;
    }

    // Subscribe to all speaking users
    const subscribedUsers = new Set<string>();

    connection.receiver.speaking.on("start", (userId) => {
      if (this.client.user?.id === userId) return; // ignore self
      if (subscribedUsers.has(userId)) return; // already subscribed

      subscribedUsers.add(userId);
      const opusStream = connection.receiver.subscribe(userId, {
        end: { behavior: EndBehaviorType.Manual },
      });
      const decoder = new OpusEncoder(48000, 2);

      opusStream.on("data", (packet: Buffer) => {
        try {
          // Decode Opus -> 48kHz stereo PCM
          const pcm48kStereo = decoder.decode(packet);
          // Resample 48kHz stereo -> 16kHz mono for Nova Sonic
          const pcm16kMono = resample48kStereoTo16kMono(pcm48kStereo);
          novaSonic.sendAudio(pcm16kMono);
        } catch { /* ignore decode errors */ }
      });

      opusStream.on("error", (err) => {
        console.error(`Opus stream error for user ${userId}:`, err);
        subscribedUsers.delete(userId);
      });

      opusStream.on("close", () => {
        subscribedUsers.delete(userId);
      });
    });
  }

  private playBuffered(session: UserSession, player: any): void {
    if (session.outputBuffer.length === 0) {
      session.isPlaying = false;
      return;
    }

    session.isPlaying = true;
    const combined = Buffer.concat(session.outputBuffer);
    session.outputBuffer = [];

    const stream = new PassThrough();
    stream.end(combined);

    const resource = createAudioResource(stream, {
      inputType: StreamType.Raw,
      inlineVolume: false,
    });
    player.play(resource);

    player.once(AudioPlayerStatus.Idle, () => {
      // Small delay to allow more audio to buffer before next play
      setTimeout(() => this.playBuffered(session, player), 50);
    });
  }

  private async disconnect(guildId: string): Promise<void> {
    const session = this.sessions.get(guildId);
    if (session) {
      await session.novaSonic.close();
      this.sessions.delete(guildId);
    }
    const connection = getVoiceConnection(guildId);
    connection?.destroy();
  }
}

// Resample 48kHz stereo (interleaved int16) -> 16kHz mono (int16)
function resample48kStereoTo16kMono(input: Buffer): Buffer {
  const samples = input.length / 4; // 2 bytes per sample, 2 channels
  const ratio = 3; // 48000 / 16000
  const outSamples = Math.floor(samples / ratio);
  const output = Buffer.alloc(outSamples * 2);

  for (let i = 0; i < outSamples; i++) {
    const srcIdx = i * ratio * 4; // position in bytes (stereo, 16-bit)
    const left = input.readInt16LE(srcIdx);
    const right = input.readInt16LE(srcIdx + 2);
    const mono = Math.round((left + right) / 2);
    output.writeInt16LE(Math.max(-32768, Math.min(32767, mono)), i * 2);
  }

  return output;
}

// Resample 24kHz mono (int16) -> 48kHz stereo (interleaved int16)
function resample24kMonoTo48kStereo(input: Buffer): Buffer {
  const samples = input.length / 2;
  const output = Buffer.alloc(samples * 2 * 4); // 2x samples, stereo (4 bytes per stereo sample)

  for (let i = 0; i < samples; i++) {
    const sample = input.readInt16LE(i * 2);
    // Duplicate each sample twice (24k -> 48k) and write to both channels
    const outIdx = i * 8; // 2 output samples * 2 channels * 2 bytes
    output.writeInt16LE(sample, outIdx);
    output.writeInt16LE(sample, outIdx + 2);
    output.writeInt16LE(sample, outIdx + 4);
    output.writeInt16LE(sample, outIdx + 6);
  }

  return output;
}
