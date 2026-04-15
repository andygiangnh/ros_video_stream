"""
S3 SPA Stack: Creates S3 bucket for hosting the single-page app and optional CloudFront distribution.
"""

import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


class S3SPAStack(cdk.Stack):
    """Stack for S3 bucket hosting the SPA."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        bucket_name: str,
        enable_cloudfront: bool = True,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create S3 bucket for SPA
        self.bucket = s3.Bucket(
            self,
            "SPABucket",
            bucket_name=bucket_name,
            versioned=True,
            public_read_access=True,
            website_index_document="index.html",
            website_error_document="index.html",
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )

        # Bucket policy for public read access
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="PublicReadGetObject",
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=["s3:GetObject"],
                resources=[self.bucket.arn_for_objects("*")],
            )
        )

        # Optional CloudFront distribution for better performance and HTTPS
        if enable_cloudfront:
            distribution = cloudfront.Distribution(
                self,
                "SPADistribution",
                default_behavior=cloudfront.BehaviorOptions(
                    origin=origins.S3StaticWebsiteOrigin(self.bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                ),
                error_responses=[
                    cloudfront.ErrorResponse(
                        http_status=404,
                        response_http_status=200,
                        response_page_path="/index.html",
                        ttl=cdk.Duration.minutes(5),
                    ),
                    cloudfront.ErrorResponse(
                        http_status=403,
                        response_http_status=200,
                        response_page_path="/index.html",
                        ttl=cdk.Duration.minutes(5),
                    ),
                ],
                default_root_object="index.html",
            )

            cdk.CfnOutput(
                self,
                "DistributionUrl",
                value=f"https://{distribution.domain_name}",
                description="CloudFront distribution URL for SPA",
            )

            cdk.CfnOutput(
                self,
                "DistributionId",
                value=distribution.distribution_id,
                description="CloudFront distribution ID",
                export_name="SPADistributionId",
            )

        # S3 website URL output
        website_url = self.bucket.bucket_website_url
        if enable_cloudfront:
            website_url = f"https://{distribution.domain_name}"

        cdk.CfnOutput(
            self,
            "BucketUrl",
            value=website_url,
            description="S3 website URL (without CloudFront)",
        )

        cdk.CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            description="S3 bucket name for SPA",
            export_name="SPABucketName",
        )
