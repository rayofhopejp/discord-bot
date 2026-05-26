1. CloudFormationスタックをデプロイ（VPC、ECS、ECRなどが作られる）
  2. Dockerイメージをビルドして、できたECRリポジトリにpush
  3. ECSサービスがそのイメージを使ってタスクを起動
  
  # 1. スタックデプロイ
  aws cloudformation deploy \
    --template-file cloudformation.yml \
    --stack-name discord-bot \
    --parameter-overrides DiscordToken=<YOUR_TOKEN> \
    --capabilities CAPABILITY_IAM
  
  # 2. ECRにログイン
  aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
  
  # 3. イメージをビルド＆push
  docker build -t
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot-bot:latest .
  docker push
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot-bot:latest
  
  # 4. ECSサービスを強制再デプロイ（新イメージを反映）
  aws ecs update-service --cluster discord-bot-cluster --service <SERVICE_NAME>
  --force-new-deployment
  
  pushするのはGitリポジトリではなく、Dockerイメージです。docker build
  でこのディレクトリからイメージを作り、それをECRにpushします。
  
  初回デプロイ時はECRにイメージがまだないのでタスクが起動失敗しますが、push後に
  サービスが自動リトライして起動します（気になるなら手順3の後に手順4を実行）。
