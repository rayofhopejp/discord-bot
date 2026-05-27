#!/bin/bash
set -e

# === 設定 ===
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO_NAME="discord-sonic-bot"
SERVICE_NAME="discord-sonic-bot"
TASK_FAMILY="discord-sonic-bot"
LOG_GROUP="/ecs/discord-sonic-bot"

# 既存の CloudFormation スタック名 (discord-bot スタックのリソースを流用)
CFN_STACK_NAME="${CFN_STACK_NAME:-discord-bot}"

echo "Account: $ACCOUNT_ID, Region: $REGION"
echo "Using CloudFormation stack: $CFN_STACK_NAME"

# === CloudFormation スタックからリソース取得 ===
echo "=== Fetching resources from CloudFormation stack ==="
CLUSTER_NAME=$(aws cloudformation describe-stacks --stack-name $CFN_STACK_NAME --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text)

SUBNET_A=$(aws cloudformation describe-stack-resource --stack-name $CFN_STACK_NAME --logical-resource-id PublicSubnetA --region $REGION \
  --query "StackResourceDetail.PhysicalResourceId" --output text)
SUBNET_B=$(aws cloudformation describe-stack-resource --stack-name $CFN_STACK_NAME --logical-resource-id PublicSubnetB --region $REGION \
  --query "StackResourceDetail.PhysicalResourceId" --output text)
SG=$(aws cloudformation describe-stack-resource --stack-name $CFN_STACK_NAME --logical-resource-id BotSecurityGroup --region $REGION \
  --query "StackResourceDetail.PhysicalResourceId" --output text)
EXEC_ROLE_ARN=$(aws cloudformation describe-stack-resource --stack-name $CFN_STACK_NAME --logical-resource-id TaskExecutionRole --region $REGION \
  --query "StackResourceDetail.PhysicalResourceId" --output text)
TASK_ROLE_ARN=$(aws cloudformation describe-stack-resource --stack-name $CFN_STACK_NAME --logical-resource-id TaskRole --region $REGION \
  --query "StackResourceDetail.PhysicalResourceId" --output text)

echo "Cluster: $CLUSTER_NAME"
echo "Subnets: $SUBNET_A, $SUBNET_B"
echo "SG: $SG"
echo "Exec Role: $EXEC_ROLE_ARN"
echo "Task Role: $TASK_ROLE_ARN"

# === 1. ECR リポジトリ ===
echo "=== Creating ECR repository ==="
aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION 2>/dev/null || \
  aws ecr create-repository --repository-name $REPO_NAME --region $REGION

# === 2. Docker build & push ===
echo "=== Building and pushing Docker image ==="
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

docker build --platform linux/amd64 -t $REPO_NAME .
docker tag $REPO_NAME:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest

# === 3. CloudWatch Logs グループ ===
echo "=== Creating CloudWatch log group ==="
aws logs describe-log-groups --log-group-name-prefix $LOG_GROUP --region $REGION --query "logGroups[?logGroupName=='$LOG_GROUP']" --output text | grep -q . || \
  aws logs create-log-group --log-group-name $LOG_GROUP --region $REGION

# === 4. SSM パラメータ (DISCORD_TOKEN) ===
echo "=== Checking SSM parameter ==="
if ! aws ssm get-parameter --name /discord-sonic-bot/discord-token --region $REGION 2>/dev/null; then
  echo "ERROR: SSM parameter /discord-sonic-bot/discord-token not found."
  echo "Run: aws ssm put-parameter --name /discord-sonic-bot/discord-token --value 'YOUR_TOKEN' --type SecureString --region $REGION"
  exit 1
fi

# SSM 読み取り権限を実行ロールに追加
aws iam put-role-policy --role-name $(echo $EXEC_ROLE_ARN | awk -F'/' '{print $NF}') --policy-name ssm-read-sonic --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ssm:GetParameters", "ssm:GetParameter"],
    "Resource": "arn:aws:ssm:'$REGION':'$ACCOUNT_ID':parameter/discord-sonic-bot/*"
  }]
}'

# === 5. タスク定義登録 ===
echo "=== Registering task definition ==="
TASK_DEF=$(cat <<EOF
{
  "family": "$TASK_FAMILY",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXEC_ROLE_ARN",
  "taskRoleArn": "$TASK_ROLE_ARN",
  "containerDefinitions": [{
    "name": "$REPO_NAME",
    "image": "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:latest",
    "essential": true,
    "environment": [{"name": "AWS_REGION", "value": "$REGION"}],
    "secrets": [{"name": "DISCORD_TOKEN", "valueFrom": "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/discord-sonic-bot/discord-token"}],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "$LOG_GROUP",
        "awslogs-region": "$REGION",
        "awslogs-stream-prefix": "ecs"
      }
    }
  }]
}
EOF
)
echo "$TASK_DEF" | aws ecs register-task-definition --cli-input-json file:///dev/stdin --region $REGION

# === 6. ECS サービス作成 or 更新 ===
echo "=== Creating/updating ECS service ==="
if aws ecs describe-services --cluster $CLUSTER_NAME --services $SERVICE_NAME --region $REGION --query "services[?status=='ACTIVE']" --output text | grep -q .; then
  aws ecs update-service --cluster $CLUSTER_NAME --service $SERVICE_NAME \
    --task-definition $TASK_FAMILY --force-new-deployment --region $REGION
  echo "=== Service updated ==="
else
  aws ecs create-service \
    --cluster $CLUSTER_NAME \
    --service-name $SERVICE_NAME \
    --task-definition $TASK_FAMILY \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_A,$SUBNET_B],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --region $REGION
  echo "=== Service created ==="
fi

echo ""
echo "✅ Deploy complete! Check status with:"
echo "   aws ecs describe-services --cluster $CLUSTER_NAME --services $SERVICE_NAME --region $REGION"
