#!/bin/bash
# Deploy SPA to AWS S3

set -e

BUCKET_NAME="${1:-ros2-camera-kvs-app}"
REGION="${2:-us-east-1}"
DISTRIBUTION_ID="${3:-}"

echo "Deploying KVS Camera Viewer SPA to S3..."
echo "Bucket: $BUCKET_NAME"
echo "Region: $REGION"

# Create bucket if it doesn't exist
if ! aws s3 ls "s3://$BUCKET_NAME" --region "$REGION" 2>&1 | grep -q 'NoSuchBucket'; then
    echo "Bucket already exists: $BUCKET_NAME"
else
    echo "Creating S3 bucket: $BUCKET_NAME"
    aws s3 mb "s3://$BUCKET_NAME" --region "$REGION"
fi

# Enable static website hosting
echo "Enabling static website hosting..."
aws s3 website "s3://$BUCKET_NAME" \
    --index-document index.html \
    --error-document index.html \
    --region "$REGION"

# Upload files
echo "Uploading static files..."
aws s3 sync . "s3://$BUCKET_NAME" \
    --region "$REGION" \
    --exclude ".git/*" \
    --exclude "node_modules/*" \
    --exclude "deploy.sh" \
    --exclude "package.json" \
    --cache-control "public, max-age=3600"

# Set proper content types
aws s3 cp "s3://$BUCKET_NAME/index.html" "s3://$BUCKET_NAME/index.html" \
    --region "$REGION" \
    --metadata-directive REPLACE \
    --cache-control "public, max-age=300" \
    --content-type "text/html" \
    --sse AES256

aws s3 cp "s3://$BUCKET_NAME/client.js" "s3://$BUCKET_NAME/client.js" \
    --region "$REGION" \
    --metadata-directive REPLACE \
    --cache-control "public, max-age=300" \
    --content-type "application/javascript" \
    --sse AES256

aws s3 cp "s3://$BUCKET_NAME/styles.css" "s3://$BUCKET_NAME/styles.css" \
    --region "$REGION" \
    --metadata-directive REPLACE \
    --cache-control "public, max-age=3600" \
    --content-type "text/css" \
    --sse AES256

# Invalidate CloudFront distribution if provided
if [ -n "$DISTRIBUTION_ID" ]; then
    echo "Invalidating CloudFront distribution: $DISTRIBUTION_ID"
    aws cloudfront create-invalidation \
        --distribution-id "$DISTRIBUTION_ID" \
        --paths "/*"
fi

# Get website URL
WEBSITE_URL="http://$BUCKET_NAME.s3-website-$REGION.amazonaws.com"
echo ""
echo "✓ Deployment complete!"
echo "Access your app at: $WEBSITE_URL"
