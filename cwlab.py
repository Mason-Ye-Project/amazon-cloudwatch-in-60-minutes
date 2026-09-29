#!/usr/bin/env python3
"""A bounded CloudWatch lab. Requires Python 3.10+ and authenticated AWS CLI v2."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

OWNER = "cloudwatch-60-book"
NAMESPACE = "BookLabs/CloudWatch60"
METRIC = "CheckoutErrors"
DIMENSIONS = [{"Name": "Application", "Value": "synthetic-checkout"}]
STATE = Path(".cwlab-state.json")
LIMITS = {"calls": 400, "ingestions": 3, "queries": 20, "datapoints": 50}
QUERIES = {
    "errors": 'fields @timestamp, level, operation, error_code, correlation_id '
              '| filter level = "ERROR" | sort @timestamp asc | limit 20',
    "latency": 'stats count(*) as requests, avg(duration_ms) as mean_ms, '
               'pct(duration_ms, 95) as p95_ms by operation',
    "levels": 'stats count(*) as events by level | sort events desc',
}


class LabError(Exception):
    pass


class AwsError(LabError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"AWS operation failed: {code}. No private response printed.")


def save(state):
    temp = STATE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(state, stream, indent=2)
    os.replace(temp, STATE)


def load():
    if not STATE.exists():
        raise LabError("No lab state. Run setup first from this same directory.")
    state = json.loads(STATE.read_text())
    run = state.get("run", "")
    if not re.fullmatch(r"cw60-[a-f0-9]{12}", run):
        raise LabError("Invalid lab resource name.")
    if state.get("owner") != OWNER or state.get("region") != "us-east-1":
        raise LabError("Unexpected owner or region in local state.")
    if state.get("group") != f"/booklabs/{run}" or state.get("alarm") != run:
        raise LabError("Unexpected resource scope in local state.")
    return state


def spend(state, key, amount=1):
    used = state.setdefault("usage", {}).get(key, 0)
    if used + amount > LIMITS[key]:
        raise LabError(f"Lab {key} limit reached. Do not reset state to bypass it.")
    state["usage"][key] = used + amount
    save(state)


def aws(state, service, operation, payload=None, cleanup=False):
    if not cleanup:
        spend(state, "calls")
    env = dict(os.environ, AWS_PAGER="", AWS_MAX_ATTEMPTS="2", AWS_RETRY_MODE="standard")
    command = ["aws", service, operation, "--region", state["region"],
               "--output", "json", "--no-cli-pager", "--cli-connect-timeout", "10",
               "--cli-read-timeout", "30"]
    if payload is not None:
        command += ["--cli-input-json", json.dumps(payload)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=70)
    except (OSError, subprocess.TimeoutExpired):
        raise LabError("AWS CLI unavailable or timed out. Check access; retain local state for cleanup.") from None
    if result.returncode:
        match = re.search(r"An error occurred \(([A-Za-z0-9_.-]+)\)", result.stderr)
        raise AwsError(match.group(1) if match else "CLIError")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def identity(state, cleanup=False):
    who = aws(state, "sts", "get-caller-identity", cleanup=cleanup)
    arn = who.get("Arn", "")
    if arn.endswith(":root"):
        raise LabError("Refusing a root-user session. Use an authorized IAM role or user.")
    if state.get("account") and state["account"] != who.get("Account"):
        raise LabError("Account differs from the account that created this lab.")
    state["account"] = who["Account"]
    save(state)


def tags(state):
    return {"BookLab": OWNER, "LabRun": state["run"]}


def require_group(state, cleanup=False):
    got = aws(state, "logs", "list-tags-log-group", {"logGroupName": state["group"]}, cleanup)
    if any(got.get("tags", {}).get(k) != v for k, v in tags(state).items()):
        raise LabError("Log group ownership tags do not match. Refusing to change it.")


def setup():
    if STATE.exists():
        raise LabError("State already exists. Resume this lab or clean it; do not overwrite it.")
    run = "cw60-" + uuid.uuid4().hex[:12]
    state = {"owner": OWNER, "run": run, "region": "us-east-1",
             "group": "/booklabs/" + run, "alarm": run, "usage": {},
             "created": int(time.time()), "closed": False}
    save(state)
    identity(state)
    # State precedes creation so that a later error still leaves a cleanup locator.
    aws(state, "logs", "create-log-group", {"logGroupName": state["group"],
                                            "logGroupClass": "STANDARD", "tags": tags(state)})
    require_group(state)
    aws(state, "logs", "put-retention-policy", {"logGroupName": state["group"], "retentionInDays": 1})
    aws(state, "logs", "create-log-stream", {"logGroupName": state["group"], "logStreamName": "checkout"})
    print("SETUP PASS: one tagged Standard log group, one stream, one-day retention.")


def fixture(now_ms):
    events = []
    for index in range(30):
        failed = index in (7, 19, 23)
        record = {"level": "ERROR" if failed else "INFO", "operation": "checkout",
                  "correlation_id": f"demo-{index:03d}", "duration_ms": 850 if failed else 40 + index,
                  "error_code": "PAYMENT_TIMEOUT" if failed else "NONE", "synthetic": True}
        events.append({"timestamp": now_ms - (30 - index) * 1000,
                       "message": json.dumps(record, separators=(",", ":"))})
    return events


def ingest(state):
    require_group(state)
    events = fixture(int(time.time() * 1000))
    if sum(len(e["message"].encode()) + 26 for e in events) > 100_000:
        raise LabError("Fixture unexpectedly exceeds the lab data limit.")
    spend(state, "ingestions")
    result = aws(state, "logs", "put-log-events", {"logGroupName": state["group"],
                 "logStreamName": "checkout", "logEvents": events})
    if result.get("rejectedLogEventsInfo") or result.get("rejectedEntityInfo"):
        raise LabError("AWS rejected part of the batch. Do not claim a complete ingestion.")
    print("INGEST PASS: 30 synthetic events accepted, including 3 demonstration errors.")


def query(state, name):
    require_group(state)
    spend(state, "queries")
    now = int(time.time())
    qid = aws(state, "logs", "start-query", {"logGroupName": state["group"],
              "startTime": state["created"] - 120, "endTime": now + 1,
              "queryString": QUERIES[name], "limit": 50})["queryId"]
    state["active_query"] = qid
    save(state)
    status = "Scheduled"
    try:
        for _ in range(24):
            result = aws(state, "logs", "get-query-results", {"queryId": qid})
            status = result["status"]
            if status == "Complete":
                # Never print @ptr: it is an opaque reference to the private account log record.
                rows = [{x["field"]: x["value"] for x in row if x["field"] != "@ptr"}
                        for row in result.get("results", [])]
                print(json.dumps({"status": status, "query": name, "rows": rows,
                       "bytesScanned": result.get("statistics", {}).get("bytesScanned", 0)}, indent=2))
                return
            if status not in ("Scheduled", "Running"):
                raise LabError(f"Query ended with {status}.")
            time.sleep(2)
        raise LabError("Query wait expired. Retry a query later; do not start unbounded polling.")
    finally:
        if status in ("Scheduled", "Running"):
            aws(state, "logs", "stop-query", {"queryId": qid}, cleanup=True)
        state.pop("active_query", None)
        save(state)


def alarm_spec(state):
    return {"AlarmName": state["alarm"], "AlarmDescription": OWNER + " " + state["run"],
            "ActionsEnabled": False, "AlarmActions": [], "OKActions": [], "InsufficientDataActions": [],
            "Namespace": NAMESPACE, "MetricName": METRIC, "Dimensions": DIMENSIONS,
            "Statistic": "Sum", "Period": 60, "EvaluationPeriods": 1, "DatapointsToAlarm": 1,
            "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching", "Unit": "Count",
            "Tags": [{"Key": k, "Value": v} for k, v in tags(state).items()]}


def require_alarm(state, alarm):
    got = aws(state, "cloudwatch", "list-tags-for-resource", {"ResourceARN": alarm["AlarmArn"]}, cleanup=True)
    actual = {t["Key"]: t["Value"] for t in got.get("Tags", [])}
    if any(actual.get(k) != v for k, v in tags(state).items()):
        raise LabError("Alarm ownership tags differ. Refusing to modify or delete it.")


def metric(state, value):
    require_group(state)
    existing = aws(state, "cloudwatch", "describe-alarms", {"AlarmNames": [state["alarm"]]})
    alarms = existing.get("MetricAlarms", [])
    if existing.get("CompositeAlarms"):
        raise LabError("Unexpected composite alarm collision.")
    if alarms:
        require_alarm(state, alarms[0])
    else:
        aws(state, "cloudwatch", "put-metric-alarm", alarm_spec(state))
    spend(state, "datapoints")
    aws(state, "cloudwatch", "put-metric-data", {"Namespace": NAMESPACE, "MetricData": [
        {"MetricName": METRIC, "Dimensions": DIMENSIONS, "Unit": "Count",
         "StorageResolution": 60, "Value": value}]})
    print(f"METRIC PASS: submitted value {value}; alarm has no external actions. Observe after evaluation.")


def observe(state):
    now = dt.datetime.now(dt.timezone.utc)
    result = aws(state, "cloudwatch", "get-metric-statistics", {"Namespace": NAMESPACE,
           "MetricName": METRIC, "Dimensions": DIMENSIONS, "Unit": "Count", "Period": 60,
           "StartTime": (now - dt.timedelta(minutes=20)).isoformat(), "EndTime": now.isoformat(),
           "Statistics": ["Sum"]})
    alarm = aws(state, "cloudwatch", "describe-alarms", {"AlarmNames": [state["alarm"]]})
    print(json.dumps({"metric": METRIC, "datapoints": result.get("Datapoints", []),
        "alarmStates": [a["StateValue"] for a in alarm.get("MetricAlarms", [])]}, indent=2))


def cleanup(state):
    # Preflight ALL existing resources before deleting any. AccessDenied is never absence.
    group_exists = True
    try:
        require_group(state, cleanup=True)
    except AwsError as exc:
        if exc.code != "ResourceNotFoundException":
            raise
        group_exists = False
    result = aws(state, "cloudwatch", "describe-alarms", {"AlarmNames": [state["alarm"]]}, cleanup=True)
    if result.get("CompositeAlarms"):
        raise LabError("Unexpected composite alarm; cleanup refused.")
    for alarm in result.get("MetricAlarms", []):
        require_alarm(state, alarm)
    if state.get("active_query"):
        aws(state, "logs", "stop-query", {"queryId": state["active_query"]}, cleanup=True)
    if result.get("MetricAlarms"):
        aws(state, "cloudwatch", "delete-alarms", {"AlarmNames": [state["alarm"]]}, cleanup=True)
    if group_exists:
        aws(state, "logs", "delete-log-group", {"logGroupName": state["group"]}, cleanup=True)
    remaining = aws(state, "cloudwatch", "describe-alarms", {"AlarmNames": [state["alarm"]]}, cleanup=True)
    if remaining.get("MetricAlarms") or remaining.get("CompositeAlarms"):
        raise LabError("Alarm deletion is not yet confirmed. Run cleanup again.")
    try:
        require_group(state, cleanup=True)
    except AwsError as exc:
        if exc.code != "ResourceNotFoundException":
            raise
    else:
        raise LabError("Log group deletion is not yet confirmed. Run cleanup again.")
    state["closed"] = True
    state.pop("active_query", None)
    save(state)
    print("CLEANUP PASS: owned log group and alarm absent. Publishing stopped; metric history expires naturally.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "ingest", "query", "metric", "observe", "cleanup"])
    parser.add_argument("--query", choices=QUERIES, default="errors")
    parser.add_argument("--value", type=int, choices=[0, 3], default=3)
    args = parser.parse_args()
    if args.action == "setup":
        setup()
        return
    state = load()
    if state.get("closed") and args.action != "cleanup":
        raise LabError("Lab is closed. Retain state as the usage record.")
    if args.action != "cleanup" and time.time() - state["created"] > 7200:
        raise LabError("Two-hour experiment window expired. Run cleanup.")
    identity(state, cleanup=args.action == "cleanup")
    if args.action == "query":
        query(state, args.query)
    elif args.action == "metric":
        metric(state, args.value)
    else:
        {"ingest": ingest, "observe": observe, "cleanup": cleanup}[args.action](state)


if __name__ == "__main__":
    try:
        main()
    except (LabError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, LabError) else "Invalid local state or response; inspect privately.", file=sys.stderr)
        sys.exit(1)
