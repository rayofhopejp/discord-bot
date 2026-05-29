# Linux 自律成長エージェント

Linuxコンテナ上で自律的に行動・学習し、10分ごとにDiscordにスクリーンショット付きで報告するBot。

## アーキテクチャ

```
┌─────────────────────────────────────────┐
│  ECS Task                               │
│  ┌──────────────┐  ┌──────────────┐    │
│  │   agent      │  │     bot      │    │
│  │ (自律行動)    │  │ (Discord連携) │    │
│  └──────┬───────┘  └──────┬───────┘    │
│         │   /shared/       │            │
│         └──────────────────┘            │
└─────────────────────────────────────────┘
```

- **agent**: Claudeが自律的にコマンド実行・Web検索・ブラウジングを行い成長
- **bot**: エージェントの報告をDiscordに送信、Discordメッセージをエージェントに渡す

## 使えるツール

| ツール | 説明 |
|--------|------|
| run_command | Linuxコマンド実行 |
| web_search | Tavily検索 |
| browse_url | Playwrightでページ閲覧+スクリーンショット |
| save_note | 学んだことをメモリに保存 |

## ローカル実行

```bash
cd linux/
cp .env.example .env
# .envを編集
docker compose up --build
```

## 環境変数

| 変数 | 説明 |
|------|------|
| LINUX_BOT_TOKEN | Discord Botトークン |
| REPORT_CHANNEL | 報告先チャンネルID |
| AWS_REGION | Bedrock用リージョン |
| AWS_ACCESS_KEY_ID | AWS認証 (ローカル用) |
| AWS_SECRET_ACCESS_KEY | AWS認証 (ローカル用) |
| TAVILY_API_KEY | Tavily API Key |

## AWSデプロイ

```bash
# ECRリポジトリ作成
aws ecr create-repository --repository-name linux-agent --region ap-northeast-1

# イメージビルド&push
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-northeast-1.amazonaws.com
docker build -t <ACCOUNT_ID>.dkr.ecr.ap-northeast-1.amazonaws.com/linux-agent:latest .
docker push <ACCOUNT_ID>.dkr.ecr.ap-northeast-1.amazonaws.com/linux-agent:latest

# スタックデプロイ (既存discord-botスタックのVPC/Subnet/SGを指定)
aws cloudformation deploy \
  --template-file cloudformation.yml \
  --stack-name linux-agent \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    LinuxBotToken=<TOKEN> \
    ReportChannel=<CHANNEL_ID> \
    TavilyApiKey=<KEY> \
    LinuxImageUri=<ECR_URI> \
    VpcId=<VPC_ID> \
    SubnetA=<SUBNET_A> \
    SubnetB=<SUBNET_B> \
    SecurityGroupId=<SG_ID>
```
