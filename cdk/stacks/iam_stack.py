"""IAM stack for ROS2 KVS publisher access."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct


class IAMStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, stream_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self,
            "Ros2KvsPublisherRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Role for ROS2 node publishing frames to Kinesis Video Streams",
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "kinesisvideo:DescribeStream",
                    "kinesisvideo:GetDataEndpoint",
                    "kinesisvideo:PutMedia",
                    "kinesisvideo:ListStreams",
                    "kinesisvideo:CreateStream",
                ],
                resources=[
                    f"arn:aws:kinesisvideo:{self.region}:{self.account}:stream/{stream_name}/*",
                    "*",
                ],
            )
        )

        cdk.CfnOutput(
            self,
            "Ros2KvsPublisherRoleArn",
            value=role.role_arn,
            description="IAM role ARN for ROS2 KVS publishing",
        )
