# Discord Sonic Bot

Amazon Nova Sonic 2 を使った Discord ボイスチャットボット。ボイスチャンネルに参加して、リアルタイムで音声会話します。

## 仕組み

```
ユーザーの音声 → Discord (Opus) → Bot (PCM 48kHz→16kHz) → Nova Sonic 2
                                                                    ↓
ユーザーに再生 ← Discord (Opus) ← Bot (PCM 24kHz→48kHz) ← Nova Sonic 2
```

## セットアップ

### 前提条件

- Node.js 22+
- Discord Bot Token ([Discord Developer Portal](https://discord.com/developers/applications) で作成)
- AWS アカウント (Bedrock の Nova Sonic 2 へのアクセス権限)

### Discord Bot の設定

1. [Discord Developer Portal](https://discord.com/developers/applications) で Application を作成
2. Bot タブで Token を取得
3. OAuth2 → URL Generator で以下の権限を付与:
   - Scopes: `bot`
   - Bot Permissions: `Connect`, `Speak`, `Send Messages`, `Read Message History`
4. 生成された URL でサーバーに招待

### インストール

```bash
git clone <repo-url>
cd discord-sonic-bot
npm install
cp .env.example .env
# .env を編集して DISCORD_TOKEN と AWS 認証情報を設定
```

### 実行

```bash
npm run build
npm start
```

### 使い方

1. Discord のボイスチャンネルに入る
2. テキストチャンネルで `!join` と入力
3. ボットがボイスチャンネルに参加して会話開始
4. `!leave` で退出

## AWS デプロイ (ECS Fargate)

### 1. ECR リポジトリ作成 & イメージ push

```bash
aws ecr create-repository --repository-name discord-sonic-bot --region us-east-1

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

docker build -t discord-sonic-bot .
docker tag discord-sonic-bot:latest ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/discord-sonic-bot:latest
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/discord-sonic-bot:latest
```

### 2. SSM パラメータにシークレットを保存

```bash
aws ssm put-parameter --name /discord-sonic-bot/discord-token --value "YOUR_TOKEN" --type SecureString
aws ssm put-parameter --name /discord-sonic-bot/aws-access-key-id --value "YOUR_KEY" --type SecureString
aws ssm put-parameter --name /discord-sonic-bot/aws-secret-access-key --value "YOUR_SECRET" --type SecureString
```

### 3. ECS タスク定義を登録 & サービス作成

`ecs-task-definition.json` の `ACCOUNT_ID` と `REGION` を置換してから:

```bash
aws ecs register-task-definition --cli-input-json file://ecs-task-definition.json
aws ecs create-service \
  --cluster default \
  --service-name discord-sonic-bot \
  --task-definition discord-sonic-bot \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"
```

## 環境変数

| 変数 | 必須 | 説明 |
|------|------|------|
| `DISCORD_TOKEN` | ✅ | Discord Bot Token |
| `AWS_REGION` | | AWS リージョン (デフォルト: us-east-1) |
| `AWS_ACCESS_KEY_ID` | ✅ | AWS アクセスキー |
| `AWS_SECRET_ACCESS_KEY` | ✅ | AWS シークレットキー |
| `AWS_SESSION_TOKEN` | | 一時認証情報の場合 |
| `NOVA_SONIC_VOICE_ID` | | 音声 ID (デフォルト: tiffany) |
| `NOVA_SONIC_SYSTEM_PROMPT` | | システムプロンプト |

## タスクロールの IAM ポリシー

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModelWithBidirectionalStream",
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0"
    }
  ]
}
```
