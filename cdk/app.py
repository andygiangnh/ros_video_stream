#!/usr/bin/env python3
"""
CDK App for KVS Camera Streaming Infrastructure.
Provisions AWS services for ROS2 camera streaming via Kinesis Video Streams.
"""

import os

import aws_cdk as cdk
from stacks.kvs_stack import KVSStack
from stacks.s3_spa_stack import S3SPAStack
from stacks.iam_stack import IAMStack

app = cdk.App()

# Get configuration from context or use defaults
stream_name = app.node.try_get_context("streamName") or "ros2-camera-stream"
bucket_name = app.node.try_get_context("bucketName") or "ros2-camera-kvs-app"
enable_cloudfront = app.node.try_get_context("enableCloudFront") or True
region = app.node.try_get_context("region") or "us-east-1"
account = (
    app.node.try_get_context("account")
    or os.environ.get("CDK_DEFAULT_ACCOUNT")
    or os.environ.get("AWS_ACCOUNT_ID")
)

if not account:
    raise ValueError(
        "AWS account is not set. Run with an SSO profile, or pass -c account=<account-id> "
        "and make sure aws sts get-caller-identity works after aws sso login."
    )

env = cdk.Environment(account=account, region=region)

# IAM stack (creates roles for ROS2 nodes)
iam_stack = IAMStack(
    app, 
    "KVSCameraIAMStack",
    env=env,
    stream_name=stream_name,
)

# KVS stack (creates Kinesis Video Stream)
kvs_stack = KVSStack(
    app,
    "KVSCameraStreamStack",
    env=env,
    stream_name=stream_name,
)

# S3 + CloudFront stack (hosts SPA)
s3_stack = S3SPAStack(
    app,
    "KVSCameraSPAStack",
    env=env,
    bucket_name=bucket_name,
    enable_cloudfront=enable_cloudfront,
)

# Add tags
cdk.Tags.of(app).add("Project", "ROS2-KVS-Camera")
cdk.Tags.of(app).add("ManagedBy", "CDK")

app.synth()
