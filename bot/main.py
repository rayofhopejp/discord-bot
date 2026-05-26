import discord
import os
import json
import boto3
from dotenv import load_dotenv

load_dotenv('../.env')

TOKEN = os.getenv('TOKEN')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
SERIFU = os.getenv('SERIFU', '')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)


def ask_claude(prompt):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }
    if SERIFU:
        body["system"] = f"以下のセリフを参考にして、そのキャラクターになりきって返答してください。\n\n{SERIFU}"
    response = bedrock.invoke_model(
        modelId='anthropic.claude-3-5-sonnet-20241022-v2:0',
        body=json.dumps(body)
    )
    result = json.loads(response['body'].read())
    return result['content'][0]['text']


@client.event
async def on_ready():
    print('ログインしました')


@client.event
async def on_message(message):
    if message.author.bot:
        return

    async with message.channel.typing():
        reply = ask_claude(message.content)

    # Discordの2000文字制限に対応
    for i in range(0, len(reply), 2000):
        await message.reply(reply[i:i+2000])

client.run(TOKEN)
