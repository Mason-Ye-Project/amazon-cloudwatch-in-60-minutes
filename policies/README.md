# Example policy

Example access policy scoped to the actions this companion calls. Replace ACCOUNT_ID with your account number. Documentation-reviewed against the AWS service-authorization references, NOT certified by a run under this identity. Test in your own account before relying on it. See list_logs.html and list_cloudwatch.html in the AWS Service Authorization Reference.

The JSON contains only IAM policy-language keys. The account placeholder must be replaced before use.

Example access policy scoped to the actions this companion calls. Replace ACCOUNT_ID with your account number. Documentation-reviewed against the AWS service-authorization references (list_logs.html, list_cloudwatch.html), NOT certified by a run under this identity. Test in your own account before relying on it. logs:GetQueryResults now supports log-group scoping; logs:StopQuery has no resource type. The metric read the companion calls is GetMetricStatistics only; GetMetricData/ListMetrics are not called and, if used for supplemental diagnostics, should be granted in a separate optional statement. logs:ListTagsLogGroup is deprecated but supported; migrate to logs:ListTagsForResource when the code does.
