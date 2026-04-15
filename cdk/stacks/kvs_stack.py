""""""





"""KVS stack resources for ROS2 camera stream ingestion."""

import aws_cdk as cdk
from aws_cdk import aws_kinesisvideo as kinesisvideo
from constructs import Construct


class KVSStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, stream_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.stream = kinesisvideo.CfnStream(
            self,
            "Ros2CameraKvsStream",
            name=stream_name,
            data_retention_in_hours=24,
            media_type="video/h264",
            device_name="ros2-camera",
            tags=[
                cdk.CfnTag(key="Name", value=stream_name),
                cdk.CfnTag(key="Project", value="ROS2-KVS-Camera"),
            ],
        )

        cdk.CfnOutput(
            self,
            "KvsStreamArn",
            value=self.stream.attr_arn,
            description="Kinesis Video Stream ARN",
            export_name="KvsStreamArn",
        )

        cdk.CfnOutput(
            self,
            "KvsStreamName",
            value=stream_name,
            description="Kinesis Video Stream name",
            export_name="KvsStreamName",
        )
