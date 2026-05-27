import {
  BedrockRuntimeClient,
  InvokeModelWithBidirectionalStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { NodeHttp2Handler } from "@smithy/node-http-handler";
import { Buffer } from "node:buffer";
import { randomUUID } from "node:crypto";
import { Subject, firstValueFrom } from "rxjs";
import { take } from "rxjs/operators";

const MODEL_ID = "amazon.nova-2-sonic-v1:0";

interface SessionConfig {
  region: string;
  credentials?: { accessKeyId: string; secretAccessKey: string; sessionToken?: string };
  voiceId?: string;
  systemPrompt?: string;
}

export class NovaSonicSession {
  private client: BedrockRuntimeClient;
  private queue: any[] = [];
  private queueSignal = new Subject<void>();
  private closeSignal = new Subject<void>();
  private promptName = randomUUID();
  private audioContentId = randomUUID();
  private isActive = false;
  private voiceId: string;
  private systemPrompt: string;

  public onAudioOutput?: (audioBase64: string) => void;
  public onTextOutput?: (text: string, role: string) => void;
  public onError?: (error: any) => void;

  constructor(config: SessionConfig) {
    this.voiceId = config.voiceId || "tiffany";
    this.systemPrompt = config.systemPrompt ||
      "You are a friendly assistant in a Discord voice channel. Keep responses short and conversational.";

    this.client = new BedrockRuntimeClient({
      region: config.region,
      ...(config.credentials && { credentials: config.credentials }),
      requestHandler: new NodeHttp2Handler({
        requestTimeout: 300000,
        sessionTimeout: 300000,
        disableConcurrentStreams: false,
        maxConcurrentStreams: 20,
      }),
    });
  }

  async start(): Promise<void> {
    this.isActive = true;

    // Session start
    this.enqueue({
      event: { sessionStart: { inferenceConfiguration: { maxTokens: 1024, topP: 0.9, temperature: 0.7 } } },
    });

    // Prompt start
    this.enqueue({
      event: {
        promptStart: {
          promptName: this.promptName,
          textOutputConfiguration: { mediaType: "text/plain" },
          audioOutputConfiguration: {
            mediaType: "audio/lpcm",
            sampleRateHertz: 24000,
            sampleSizeBits: 16,
            channelCount: 1,
            voiceId: this.voiceId,
            encoding: "base64",
            audioType: "SPEECH",
          },
        },
      },
    });

    // System prompt
    const textId = randomUUID();
    this.enqueue({ event: { contentStart: { promptName: this.promptName, contentName: textId, type: "TEXT", interactive: false, role: "SYSTEM", textInputConfiguration: { mediaType: "text/plain" } } } });
    this.enqueue({ event: { textInput: { promptName: this.promptName, contentName: textId, content: this.systemPrompt } } });
    this.enqueue({ event: { contentEnd: { promptName: this.promptName, contentName: textId } } });

    // Audio content start
    this.enqueue({
      event: {
        contentStart: {
          promptName: this.promptName,
          contentName: this.audioContentId,
          type: "AUDIO",
          interactive: true,
          role: "USER",
          audioInputConfiguration: {
            mediaType: "audio/lpcm",
            sampleRateHertz: 16000,
            sampleSizeBits: 16,
            channelCount: 1,
            audioType: "SPEECH",
            encoding: "base64",
          },
        },
      },
    });

    // Start bidirectional stream
    const asyncIterable = this.createAsyncIterable();
    const response = await this.client.send(
      new InvokeModelWithBidirectionalStreamCommand({ modelId: MODEL_ID, body: asyncIterable })
    );

    this.processResponses(response).catch((err) => {
      console.error("Response processing error:", err);
      this.onError?.(err);
    });
  }

  sendAudio(pcmBuffer: Buffer): void {
    if (!this.isActive) return;
    this.enqueue({
      event: {
        audioInput: {
          promptName: this.promptName,
          contentName: this.audioContentId,
          content: pcmBuffer.toString("base64"),
        },
      },
    });
  }

  async close(): Promise<void> {
    if (!this.isActive) return;
    this.isActive = false;

    this.enqueue({ event: { contentEnd: { promptName: this.promptName, contentName: this.audioContentId } } });
    this.enqueue({ event: { promptEnd: { promptName: this.promptName } } });
    this.enqueue({ event: { sessionEnd: {} } });

    // Give time for events to flush
    await new Promise((r) => setTimeout(r, 500));
    this.closeSignal.next();
    this.closeSignal.complete();
  }

  get active(): boolean {
    return this.isActive;
  }

  private enqueue(event: any): void {
    this.queue.push(event);
    this.queueSignal.next();
  }

  private createAsyncIterable(): AsyncIterable<any> {
    return {
      [Symbol.asyncIterator]: () => ({
        next: async (): Promise<IteratorResult<any>> => {
          if (!this.isActive && this.queue.length === 0) {
            return { value: undefined, done: true };
          }

          if (this.queue.length === 0) {
            try {
              await Promise.race([
                firstValueFrom(this.queueSignal.pipe(take(1))),
                firstValueFrom(this.closeSignal.pipe(take(1))).then(() => { throw new Error("closed"); }),
              ]);
            } catch {
              if (this.queue.length === 0) return { value: undefined, done: true };
            }
          }

          const event = this.queue.shift();
          if (!event) return { value: undefined, done: true };

          return {
            value: { chunk: { bytes: new TextEncoder().encode(JSON.stringify(event)) } },
            done: false,
          };
        },
      }),
    };
  }

  private async processResponses(response: any): Promise<void> {
    let role = "";
    for await (const event of response.body) {
      if (!this.isActive && this.queue.length === 0) break;

      if (event.chunk?.bytes) {
        try {
          const json = JSON.parse(new TextDecoder().decode(event.chunk.bytes));
          const evt = json.event;
          if (!evt) continue;

          if (evt.contentStart) {
            role = evt.contentStart.role || role;
          } else if (evt.audioOutput) {
            this.onAudioOutput?.(evt.audioOutput.content);
          } else if (evt.textOutput) {
            this.onTextOutput?.(evt.textOutput.content, role);
          }
        } catch { /* ignore parse errors */ }
      }
    }
  }
}
