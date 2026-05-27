# デプロイ方法
## Discord アプリを作る
権限は以下の権限を与えます。
Send Messages, Read Message History, Add Reactions, Attach Files
Permissions Integer は 68672 になります。

トークンをコピーして保管しておきます。
Oauthリンクを作成し、そこからサーバーにインストールをします。（選んだ権限が出てくるはず）

## AWS へのデプロイ
### 1. ECRリポジトリを手動作成
```
aws ecr create-repository --repository-name discord-bot --region ap-northeast-1
```
### 2. イメージをビルド＆push
```
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker build -t <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot:latest .
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot:latest
```

### 3. スタックデプロイ
```
aws cloudformation deploy --template-file cloudformation.yml --stack-name discord-bot --parameter-overrides DiscordToken=<YOUR_TOKEN> AwsRegionName=ap-northeast-1 AllowedChannels=<Channel IDs comma separated> TavilyApiKey=<API KEY> BotImageUri=<URI of ECR image>
```



