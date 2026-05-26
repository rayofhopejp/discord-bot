 # 1. ECRリポジトリを手動作成
  aws ecr create-repository --repository-name discord-bot --region us-east-1
  
  # 2. イメージをビルド＆push
  aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
  docker build -t
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot:latest .
  docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discord-bot:latest
  
  # 3. スタックデプロイ
  aws cloudformation deploy \
    --template-file cloudformation.yml \
    --stack-name discord-bot \
    --parameter-overrides \
      DiscordToken=<YOUR_TOKEN> \
  
