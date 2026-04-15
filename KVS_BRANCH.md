# KVS Branch - AWS Kinesis Video Streams Integration

Welcome to the `kvs` branch! This branch implements cloud-based camera streaming using AWS services.

## Quick Overview

```
Camera (local)
    ↓
camera_publisher_node (ROS 2)
    ↓
/camera/image_raw (ROS 2 topic)
    ↓
kvs_bridge_node (AWS KVS integration)
    ↓
AWS Kinesis Video Streams
    ↓
Single-Page App (S3 + CloudFront)
    ↓
Browser
```

## Key Improvements Over Main Branch

| Feature | Main | KVS |
|---------|------|-----|
| **Deployment** | Local network only | Cloud-hosted |
| **Access** | LAN IP only | Global via CloudFront |
| **Scalability** | Single WebRTC server | AWS managed service |
| **Security** | Manual firewall | IAM + security groups |
| **Infrastructure** | Manual Docker | AWS CDK IaC |
| **SPA Hosting** | Embedded in ROS node | S3 + CloudFront |
| **Cost** | Free (self-hosted) | ~$10-30/month |

## Project Structure

```
video_streaming/
│
├── video_streaming/
│   ├── __init__.py
│   ├── camera_publisher_node.py       # Unchanged - OpenCV camera capture
│   ├── kvs_bridge_node.py             # NEW - ROS 2 → AWS KVS bridge
│   ├── webrtc_camera_node.py          # Still available but not used here
│   └── www/
│       ├── index.html
│       └── client.js
│
├── spa/                               # NEW - Separate SPA deployment
│   ├── index.html                     # AWS KVS viewer UI
│   ├── client.js                      # KVS WebRTC client logic
│   ├── styles.css                     # Responsive styles
│   ├── package.json
│   └── deploy.sh                      # S3 deployment script
│
├── cdk/                               # NEW - Infrastructure as Code
│   ├── app.py                         # Main CDK app
│   ├── cdk.json                       # CDK config
│   ├── requirements.txt               # Python deps
│   ├── README.md                      # Comprehensive CDK guide
│   └── stacks/
│       ├── kvs_stack.py               # KVS stream resources
│       ├── s3_spa_stack.py            # S3 bucket + CloudFront
│       └── iam_stack.py               # IAM roles & policies
│
├── launch/
│   ├── stream_only.launch.py          # Original (for main branch)
│   └── kvs_stream.launch.py           # NEW - KVS pipeline launch
│
├── docker/                            # Docker files for containerization
│   ├── Dockerfile
│   ├── run_container.sh
│   └── ...
│
├── README.md                          # Main README with branch info
├── KVS_BRANCH.md                      # This file
├── IMPLEMENTATION.md                  # Original implementation guide
├── setup.py                           # Updated with kvs_bridge_node entry
├── package.xml                        # Updated with boto3 dependency
└── ...
```

## Quick Start (5 minutes)

### Prerequisites
- AWS account (free tier eligible)
- AWS CLI v2 configured
- Python 3.8+
- ROS 2 Humble
- OpenCV + camera device

### Step 1: Deploy AWS Infrastructure

```bash
cd cdk
pip install -r requirements.txt
cdk deploy --all

# Note the outputs:
# - KVSStreamArn
# - SPABucketName
# - CloudFront URL (if enabled)
```

### Step 2: Deploy SPA to S3

```bash
cd ../spa
bash deploy.sh ros2-camera-kvs-app us-east-1
```

### Step 3: Run ROS 2 Nodes

```bash
# Make sure you have AWS credentials in environment
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx

# Option A: Using launch file
ros2 launch video_streaming kvs_stream.launch.py

# Option B: Run nodes separately
ros2 run video_streaming camera_publisher_node
ros2 run video_streaming kvs_bridge_node --stream-name ros2-camera-stream
```

### Step 4: View Stream

1. Navigate to CloudFront URL from CDK output
2. Enter stream name: `ros2-camera-stream`
3. Enter region: `us-east-1`
4. Click **Connect**

## Detailed Documentation

For comprehensive documentation, see:
- [cdk/README.md](cdk/README.md) - Complete CDK deployment guide
- [spa/deploy.sh](spa/deploy.sh) - SPA deployment details
- [IMPLEMENTATION.md](IMPLEMENTATION.md) - Original architecture (for context)

## Architecture Details

### AWS Components

**Kinesis Video Streams**
- Ingests frames from ROS 2 nodes via `kvs_bridge_node`
- Buffers frames for 24 hours by default
- Provides WebRTC signaling channel for viewers

**S3 Bucket**
- Hosts the static web application (HTML, CSS, JS)
- Configured for website hosting
- Public read access for assets

**CloudFront CDN** (optional)
- Edge caching for SPA assets
- HTTPS termination
- Global distribution
- Cache invalidation on deploy

**IAM Roles & Policies**
- `ROS2KVSRole` - allows ROS 2 nodes to put media to KVS
- Instance profile for EC2 (if running on AWS)
- Least-privilege principle

### ROS 2 Components

**camera_publisher_node**
```python
# Captures frames from /dev/video0
# Publishes to /camera/image_raw
# Topic: sensor_msgs/Image
# QoS: History=Keep Last, Depth=10
```

**kvs_bridge_node** (new for KVS branch)
```python
# Subscribes to /camera/image_raw
# Puts frames to AWS KVS stream
# Handles authentication via AWS SDK
# Automatic reconnection on failure
```

### Browser Client

The SPA (`spa/client.js`) handles:
1. AWS SDK initialization with region selection
2. Getting signaling channel endpoint from KVS
3. Creating WebRTC peer connection
4. Negotiating SDP offer/answer
5. Displaying incoming video stream
6. Monitoring stats (FPS, RTT, jitter)

## AWS Services & Costs

### Monthly Cost Estimate
| Service | Usage | Cost |
|---------|-------|------|
| KVS Ingestion | 8 hrs/day, 15fps | ~$6 |
| KVS Storage | 1 day retention | ~$1 |
| CloudFront | 10 GB transfer | ~$0.85 |
| S3 | ~100 KB stored | <$0.01 |
| Data Transfer | Regional | $0 (free) |
| **Total** | | **~$8/month** |

Free tier credits may cover this for first 12 months.

### Optimization Tips

**Reduce KVS costs:**
- Lower frame rate: `fps:=10` instead of 15
- Reduce resolution: `width:=320 height:=240`
- Enable video compression in kvs_bridge_node (implement H.264)
- Reduce data retention: configure in CDK

**Reduce CloudFront costs:**
- Increase cache TTL
- Use AWS CloudFront free tier (1 TB/month)
- Disable CloudFront if in same region

## Configuration

### KVS Bridge Parameters

```bash
ros2 run video_streaming kvs_bridge_node \
  --image-topic /camera/image_raw \
  --stream-name ros2-camera-stream \
  --aws-region us-east-1 \
  --width 640 \
  --height 360 \
  --fps 15
```

### CDK Context Variables

Edit `cdk/cdk.json`:
```json
{
  "context": {
    "streamName": "ros2-camera-stream",
    "bucketName": "ros2-camera-kvs-app",
    "enableCloudFront": true,
    "region": "us-east-1"
  }
}
```

### SPA Configuration

Edit `spa/index.html` default values:
- Stream name
- AWS region
- ICE servers (for WebRTC)

## Troubleshooting

### 1. KVS Bridge Can't Connect to AWS

**Symptom:** `An error occurred (UnauthorizedOperation)`

**Solution:**
```bash
# Check credentials
aws sts get-caller-identity

# Verify IAM permissions
aws kinesisvideo describe-stream --stream-name ros2-camera-stream --region us-east-1

# Set credentials (if needed)
export AWS_PROFILE=default
export AWS_REGION=us-east-1
```

### 2. Browser Can't Load Stream

**Symptom:** Browser shows "Idle" or connection fails

**Solution:**
1. Verify ROS 2 nodes are running:
   ```bash
   ros2 topic list | grep camera
   ros2 topic echo /camera/image_raw --once
   ```

2. Check KVS bridge logs:
   ```bash
   ros2 run video_streaming kvs_bridge_node 2>&1 | grep -i "error"
   ```

3. Verify KVS stream exists:
   ```bash
   aws kinesisvideo list-streams --region us-east-1
   ```

### 3. SPA Won't Load from S3

**Symptom:** 403 Forbidden or 404 Not Found

**Solution:**
```bash
# Check S3 bucket contents
aws s3 ls s3://ros2-camera-kvs-app/

# Verify bucket policy allows public read
aws s3api get-bucket-policy --bucket ros2-camera-kvs-app

# Re-deploy SPA
cd spa && bash deploy.sh ros2-camera-kvs-app us-east-1
```

### 4. High Latency or Buffering

**Symptom:** Video lags or jitter buffer constantly grows

**Solution:**
- Reduce frame rate: `fps:=10`
- Reduce resolution: `width:=480 height:=360`
- Check network: `ping to AWS endpoint`
- Monitor CPU on ROS 2 machine

### 5. CDK Deploy Fails

**Symptom:** Permission denied or service not available

**Solution:**
```bash
# Verify CDK toolkit is bootstrapped
cdk bootstrap aws://ACCOUNT/REGION

# Check IAM user has permissions for KVS, S3, CloudFront, IAM
# Redeploy
cdk deploy --all --force
```

## Advanced Usage

### Running on EC2

```bash
# 1. Create EC2 instance
# 2. Attach ROS2KVSRole instance profile (created by IAM stack)
# 3. Install ROS 2 Humble and dependencies
# 4. Install this package
# 5. Run kvs_stream.launch.py

ros2 launch video_streaming kvs_stream.launch.py
```

### Running in Docker

```bash
# Build container with ROS 2 + kvs_bridge_node
docker build -f docker/Dockerfile -t ros2-kvs .

# Run with AWS credentials
docker run -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
           -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
           -e AWS_DEFAULT_REGION=us-east-1 \
           --device /dev/video0:/dev/video0 \
           ros2-kvs
```

### Implementing H.264 Encoding

For better compression and lower KVS costs, implement H.264 encoding in `kvs_bridge_node.py`:

```python
# Install: pip install av

import av
container = av.open('pipe:', format='h264', mode='w')
stream = container.streams.video[0]

# Encode frame
for packet in encoder.encode(frame):
    # Put packet to KVS
```

### Adding Authentication to SPA

Implement AWS Cognito or temporary credentials:

```python
# In spa/client.js
const credentials = new AWS.CognitoIdentityCredentials({
  IdentityPoolId: 'us-east-1:xxxxxxxxxxxxx'
});

AWS.config.credentials = credentials;
```

## Git Workflow

```bash
# Check current branch
git branch -v

# Switch between branches
git checkout main      # Direct WebRTC approach
git checkout kvs       # AWS KVS approach

# View branch history
git log --oneline --graph --all

# Merge KVS features back to main (if desired)
git checkout main
git merge kvs
```

## Comparison: main vs kvs

### When to Use Main Branch
- ✅ All devices on same LAN
- ✅ No AWS account needed
- ✅ Lowest cost ($0)
- ✅ Fastest deployment
- ✅ Complete local control

### When to Use KVS Branch
- ✅ Global access needed
- ✅ AWS infrastructure preferred
- ✅ Multiple remote viewers
- ✅ Enterprise security requirements
- ✅ Scalable to 1000+ concurrent viewers

## Next Steps

1. **Review the CDK stacks** - adjust resources for production
2. **Implement authentication** - add Cognito or API Gateway
3. **Set up monitoring** - CloudWatch dashboards and alarms
4. **Create CI/CD pipeline** - automate SPA deployments
5. **Optimize encoding** - implement H.264 for lower costs
6. **Add multiple streams** - support multiple cameras

## References

- [AWS Kinesis Video Streams Docs](https://docs.aws.amazon.com/kinesisvideo/)
- [AWS CDK Python Docs](https://docs.aws.amazon.com/cdk/v2/guide/work-with-cdk-python.html)
- [WebRTC for KVS](https://docs.aws.amazon.com/kinesisvideo/latest/devguide/webrtc/)
- [ROS 2 Documentation](https://docs.ros.org/)

## Support

For issues:
1. Check [cdk/README.md](cdk/README.md) for detailed debugging
2. Review AWS KVS documentation
3. Check CloudWatch logs
4. Examine browser console errors (F12)

---

**Happy streaming! 🎥**
