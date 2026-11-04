#!/bin/bash
# Starts/stops the project's EC2 box used for live SIP calling (PJSIP probe /
# run_caller.py / run_pilot.sh - see docs/sip-harness.md). This is the box
# that racks up AWS compute cost while it sits running and idle.
#
# It has an Elastic IP attached, so its public address does not change across
# a stop/start cycle - safe even if a vendor trunk allowlists this box's IP.
#
# Stopping it pauses compute billing (the big cost) but does NOT stop all
# billing: the attached EBS volume and the Elastic IP are billed as long as
# they exist, running or not.
#
# Usage:
#   scripts/aws_server.sh start    # boot it back up before a test run
#   scripts/aws_server.sh stop     # shut it down when you're done for the day
#   scripts/aws_server.sh status   # check whether it's running right now
set -euo pipefail

REGION="us-east-1"
INSTANCE_ID="i-0c20a14e85eadd900"  # Name tag: voice-test-agent-sip-probe

cmd="${1:-}"

status() {
  aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].{State:State.Name,PublicIp:PublicIpAddress,Type:InstanceType}' \
    --output table
}

case "$cmd" in
  start)
    aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
    echo "Starting $INSTANCE_ID, waiting until it's running..."
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
    status
    ;;
  stop)
    aws ec2 stop-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
    echo "Stopping $INSTANCE_ID, waiting until it's stopped..."
    aws ec2 wait instance-stopped --region "$REGION" --instance-ids "$INSTANCE_ID"
    echo "Stopped - compute billing paused. EBS volume + Elastic IP are still billed."
    ;;
  status)
    status
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
