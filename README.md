# Amazon CloudWatch in 60 Minutes — companion

This repository supports the bounded CloudWatch troubleshooting loop in
*Amazon CloudWatch in 60 Minutes* by Mason Ye.

The lab creates one tagged, Standard-class log group in `us-east-1`, streams
thirty synthetic checkout-service log events (three of them demonstration
errors), finds and aggregates them with CloudWatch Logs Insights, publishes one
custom metric, puts one no-action alarm on it and observes it evaluate, then
deletes the log group and the alarm. The custom metric cannot be deleted; it
leaves metric listings after about two weeks of no data and its data ages out
over CloudWatch's retention schedule.

## Requirements

- Python 3.10 or newer (standard library only — no third-party packages).
- AWS CLI v2, installed and on your `PATH`.
- An authenticated, **non-root** IAM identity (`aws sts get-caller-identity`
  must not return an ARN ending in `:root`).
- Permissions covering the lab's actions; see `policies/least-privilege-example.json`.

## Run it

```
python3 -m unittest discover -v      # offline tests, no AWS calls
python3 cwlab.py setup
python3 cwlab.py ingest
python3 cwlab.py query --query errors
python3 cwlab.py query --query levels
python3 cwlab.py query --query latency
python3 cwlab.py metric --value 3
python3 cwlab.py observe
python3 cwlab.py metric --value 0     # optional, in a later minute, to watch it clear
python3 cwlab.py cleanup
```

## What it contains

- `cwlab.py` — the lab, as one script driving the AWS CLI, with guardrails.
- `test_cwlab.py` — offline standard-library tests (no AWS calls).
- `queries/errors.txt`, `queries/levels.txt`, `queries/latency.txt` — the three
  canonical Logs Insights query strings the script runs.
- `policies/least-privilege-example.json` — an example access policy scoped to
  the script's actions. It is documentation-reviewed against the AWS
  service-authorization references, **not** certified by a run under that
  identity; test it in your own account before relying on it.

## Safety boundaries

- Resources live only in `us-east-1`; the log group is `/booklabs/cw60-<suffix>`.
- The log group and the alarm carry the `BookLab=cloudwatch-60-book` ownership
  tag; the script refuses to modify or delete resources whose tags do not match.
  (Custom metrics do not take resource tags.)
- The script refuses to run as the account root user.
- Self-imposed workload counters (in the private `.cwlab-state.json`): at most
  400 counted calls, 3 ingestions of at most 100 KB each, 20 Logs Insights
  queries, and 50 metric datapoints, plus a two-hour experiment window. These
  are limits in this tool, **not** an AWS spending cap. Cleanup and a few
  read-only identity/tag calls fall outside the counted total, so teardown
  always remains available.
- All data is synthetic and marked `synthetic: true`; never add real or
  sensitive data.
- No dashboard, no SNS or notifications, no continuous producers or schedules.
- `.cwlab-state.json` is private local state; it is git-ignored and must never
  be published. Keep it as your usage record rather than deleting it to rerun.

## Cost

Costs are kept small by design and are a rate-based estimate, not a posted
invoice; no CloudWatch feature caps spending automatically. Confirm current
prices at https://aws.amazon.com/cloudwatch/pricing/ and clean up promptly.

## License

Code is licensed under the Zero-Clause BSD license (see `LICENSE-CODE.txt`).
The book's prose is not covered by that license.
