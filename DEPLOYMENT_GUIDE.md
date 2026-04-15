# KVS Deployment Guide

This guide deploys the KVS-based ROS2 streaming solution, including the SPA frontend hosted in S3.

## 1) Prerequisites

- AWS account with permission to manage IAM, Kinesis Video Streams, S3, and CloudFront
- AWS CLI v2 configured
- Python 3.10+
- AWS CDK CLI installed (`npm install -g aws-cdk` or user-local install)
- ROS 2 Humble environment
- Linux camera device (for example `/dev/video0`)

If you do not have permission for global npm installs, use:

```bash
npm config set prefix "$HOME/.local"
npm install -g aws-cdk
export PATH="$HOME/.local/bin:$PATH"
```

Validate AWS auth:

```bash
aws sts get-caller-identity
```

## 2) Configure project variables

From the repository root:

```bash
cd /home/giangnh101/ros_ws/src/video_streaming

export AWS_REGION=us-east-1
export STREAM_NAME=ros2-camera-stream
export SPA_BUCKET_NAME=ros2-camera-kvs-app
```

Optional: use a unique bucket name if the default is already taken.

## 3) Provision AWS infrastructure (CDK)

```bash
cd cdk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# First time per account/region:
cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/$AWS_REGION

# Deploy with context overrides
cdk deploy --all \
  -c streamName=$STREAM_NAME \
  -c bucketName=$SPA_BUCKET_NAME \
  -c region=$AWS_REGION
```

Expected outputs include:

- KVS stream name and ARN
- SPA bucket name
- CloudFront distribution URL (if enabled)

## 4) Deploy SPA frontend to S3

From repository root:

```bash
cd /home/giangnh101/ros_ws/src/video_streaming/spa
chmod +x deploy.sh
./deploy.sh $SPA_BUCKET_NAME $AWS_REGION
```

If CloudFront is enabled and you changed frontend files, invalidate cache:

```bash
aws cloudfront create-invalidation \
  --distribution-id <YOUR_DISTRIBUTION_ID> \
  --paths "/*"
```

## 5) Build and run ROS2 nodes

From ROS workspace root:

```bash
cd /home/giangnh101/ros_ws
colcon build --packages-select video_streaming
source install/setup.bash

ros2 launch video_streaming kvs_stream.launch.py \
  stream_name:=$STREAM_NAME \
  aws_region:=$AWS_REGION \
  camera_index:=0 \
  width:=640 \
  height:=360 \
  fps:=15
```

## 6) Open the web frontend

Open one of:

- CloudFront URL from CDK output (recommended)
- `http://<bucket-name>.s3-website-<region>.amazonaws.com`

In the page:

- Set Stream Name to `$STREAM_NAME`
- Set AWS Region to `$AWS_REGION`
- Click Connect

## 7) Verification checklist

- ROS camera topic exists:

```bash
ros2 topic list | grep /camera/image_raw
```

- ROS topic has messages:

```bash
ros2 topic echo /camera/image_raw --once
```

- KVS stream exists:

```bash
aws kinesisvideo describe-stream --stream-name $STREAM_NAME --region $AWS_REGION
```

- SPA files are in S3:

```bash
aws s3 ls s3://$SPA_BUCKET_NAME/
```

## 8) AWS CLI-only provisioning alternative (without CDK)

If you prefer raw CLI, create the minimum resources manually:

```bash
aws kinesisvideo create-stream \
  --stream-name $STREAM_NAME \
  --data-retention-in-hours 24 \
  --region $AWS_REGION

aws s3 mb s3://$SPA_BUCKET_NAME --region $AWS_REGION
aws s3 website s3://$SPA_BUCKET_NAME --index-document index.html --error-document index.html

aws s3api put-bucket-policy \
  --bucket $SPA_BUCKET_NAME \
  --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"PublicRead\",\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$SPA_BUCKET_NAME/*\"}]}"
```

Then deploy frontend with:

```bash
cd /home/giangnh101/ros_ws/src/video_streaming/spa
./deploy.sh $SPA_BUCKET_NAME $AWS_REGION
```

## 9) Teardown

CDK-managed resources:

```bash
cd /home/giangnh101/ros_ws/src/video_streaming/cdk
source .venv/bin/activate
cdk destroy --all -c streamName=$STREAM_NAME -c bucketName=$SPA_BUCKET_NAME -c region=$AWS_REGION
```

CLI-managed resources:

```bash
aws s3 rm s3://$SPA_BUCKET_NAME --recursive
aws s3 rb s3://$SPA_BUCKET_NAME
aws kinesisvideo delete-stream --stream-name $STREAM_NAME --region $AWS_REGION
```
