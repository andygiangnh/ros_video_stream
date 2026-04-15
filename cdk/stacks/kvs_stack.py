""""""























































        )            export_name="KVSStreamName",            description="Name of Kinesis Video Stream",            value=self.stream.name,            "StreamName",            self,        cdk.CfnOutput(        # Output the stream name        )            export_name="KVSStreamArn",            description="ARN of Kinesis Video Stream",            value=self.stream.attr_arn,            "StreamArn",            self,        cdk.CfnOutput(        # Output the stream ARN        )            ],                cdk.CfnTag(key="Purpose", value="ROS2 Camera Streaming"),                cdk.CfnTag(key="Name", value=stream_name),            tags=[            device_name="ros2-camera",            media_type="video/h264",  # or video/h265, video/jpeg            # Media type: REALTIME for live streaming            data_retention_in_hours=24,            name=stream_name,            "ROS2CameraStream",            self,        self.stream = kinesisvideo.CfnStream(        # Create Kinesis Video Stream        super().__init__(scope, construct_id, **kwargs)    ) -> None:        **kwargs        stream_name: str,        construct_id: str,        scope: Construct,        self,    def __init__(    """Stack for Kinesis Video Stream."""class KVSStack(cdk.Stack):from constructs import Construct)    aws_kinesisvideo as kinesisvideo,from aws_cdk import (import aws_cdk as cdk"""KVS Stack: Creates AWS Kinesis Video Stream for camera data ingestion.IAM Stack: Creates IAM roles and policies for ROS2 nodes to access KVS.
"""

import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
)
from constructs import Construct


class IAMStack(cdk.Stack):
    """Stack for IAM roles and policies."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        stream_name: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Role for ROS2 nodes running locally/in containers
        ros2_role = iam.Role(
            self,
            "ROS2KVSRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Role for ROS2 nodes to put data to KVS",
        )

        # Policy to put media to KVS
        kvs_policy = iam.Policy(
            self,
            "ROS2KVSPolicy",
            roles=[ros2_role],
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kinesisvideo:PutMedia",
                        "kinesisvideo:DescribeStream",
                        "kinesisvideo:GetDataEndpoint",
                        "kinesisvideo:CreateStream",
                        "kinesisvideo:ListStreams",
                    ],
                    resources=[
                        f"arn:aws:kinesisvideo:{self.region}:{self.account}:stream/{stream_name}/*"
                    ],
                ),
            ],
        )

        # Output the role ARN for use in EC2 instance profile
        cdk.CfnOutput(
            self,
            "ROS2RoleArn",
            value=ros2_role.role_arn,
            description="ARN of ROS2 KVS Role",
            export_name="ROS2KVSRoleArn",
        )

        # Output role name for reference
        cdk.CfnOutput(
            self,
            "ROS2RoleName",
            value=ros2_role.role_name,
            description="Name of ROS2 KVS Role",
            export_name="ROS2KVSRoleName",
        )

        # Create an instance profile for EC2
        instance_profile = iam.InstanceProfile(
            self,
            "ROS2InstanceProfile",
            role=ros2_role,
        )

        cdk.CfnOutput(
            self,
            "InstanceProfileArn",
            value=instance_profile.instance_profile_arn,
            description="ARN of instance profile for EC2 instances running ROS2",
            export_name="ROS2InstanceProfileArn",
        )
