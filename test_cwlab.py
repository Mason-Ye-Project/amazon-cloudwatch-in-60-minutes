import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cwlab as lab


class CloudWatchLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_patch = patch.object(lab, "STATE", Path(self.tmp.name) / "state.json")
        self.state_patch.start()
        self.state = {"owner": lab.OWNER, "run": "cw60-012345abcdef", "region": "us-east-1",
                      "group": "/booklabs/cw60-012345abcdef", "alarm": "cw60-012345abcdef",
                      "usage": {}, "created": 1000}

    def tearDown(self):
        self.state_patch.stop()
        self.tmp.cleanup()

    def test_fixture_has_expected_errors_order_and_size(self):
        events = lab.fixture(2_000_000)
        self.assertEqual(len(events), 30)
        self.assertEqual(sum(json.loads(x["message"])["level"] == "ERROR" for x in events), 3)
        self.assertEqual(events, sorted(events, key=lambda x: x["timestamp"]))
        self.assertTrue(all(x["timestamp"] <= 2_000_000 for x in events))
        self.assertLess(sum(len(x["message"].encode()) + 26 for x in events), 10_000)

    def test_limit_persists_before_network_request(self):
        self.state["usage"]["queries"] = 19
        lab.spend(self.state, "queries")
        self.assertEqual(json.loads(lab.STATE.read_text())["usage"]["queries"], 20)
        with self.assertRaises(lab.LabError):
            lab.spend(self.state, "queries")

    def test_resource_scope_cannot_be_changed(self):
        self.state["group"] = "/production/payments"
        lab.save(self.state)
        with self.assertRaises(lab.LabError):
            lab.load()

    def test_root_identity_is_refused(self):
        with patch.object(lab, "aws", return_value={"Arn": "arn:aws:iam::000000000000:root", "Account": "000000000000"}):
            with self.assertRaises(lab.LabError):
                lab.identity(self.state)

    def test_account_switch_is_refused(self):
        self.state["account"] = "000000000000"
        with patch.object(lab, "aws", return_value={"Arn": "example:user/demo", "Account": "111111111111"}):
            with self.assertRaises(lab.LabError):
                lab.identity(self.state)

    def test_alarm_has_no_external_actions_or_high_cardinality_dimensions(self):
        spec = lab.alarm_spec(self.state)
        self.assertFalse(spec["ActionsEnabled"])
        self.assertEqual(spec["AlarmActions"], [])
        self.assertEqual(spec["Dimensions"], lab.DIMENSIONS)
        self.assertEqual(spec["Period"], 60)
        self.assertEqual(spec["TreatMissingData"], "notBreaching")

    def test_partial_ingest_is_not_success(self):
        def fake(state, service, op, payload=None, cleanup=False):
            if op == "list-tags-log-group":
                return {"tags": lab.tags(state)}
            return {"rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0}}
        with patch.object(lab, "aws", side_effect=fake):
            with self.assertRaises(lab.LabError):
                lab.ingest(self.state)

    def test_cleanup_permission_failure_deletes_nothing(self):
        with patch.object(lab, "aws", side_effect=lab.AwsError("AccessDeniedException")) as mock:
            with self.assertRaises(lab.AwsError):
                lab.cleanup(self.state)
            self.assertEqual(mock.call_count, 1)
            self.assertFalse(self.state.get("closed", False))

    def test_cleanup_checks_alarm_tags_before_any_delete(self):
        operations = []
        def fake(state, service, op, payload=None, cleanup=False):
            operations.append(op)
            if op == "list-tags-log-group":
                return {"tags": lab.tags(state)}
            if op == "describe-alarms":
                return {"MetricAlarms": [{"AlarmArn": "dummy"}]}
            return {"Tags": [{"Key": "BookLab", "Value": "another-owner"}]}
        with patch.object(lab, "aws", side_effect=fake):
            with self.assertRaises(lab.LabError):
                lab.cleanup(self.state)
        self.assertFalse(any(x.startswith("delete-") for x in operations))

    def test_cleanup_already_absent_is_safe(self):
        def fake(state, service, op, payload=None, cleanup=False):
            if op == "list-tags-log-group":
                raise lab.AwsError("ResourceNotFoundException")
            return {"MetricAlarms": []}
        with patch.object(lab, "aws", side_effect=fake), patch("builtins.print"):
            lab.cleanup(self.state)
        self.assertTrue(self.state["closed"])

    def test_completed_query_does_not_print_private_pointer_or_stop_again(self):
        operations = []
        def fake(state, service, op, payload=None, cleanup=False):
            operations.append(op)
            if op == "list-tags-log-group":
                return {"tags": lab.tags(state)}
            if op == "start-query":
                return {"queryId": "private-query"}
            return {"status": "Complete", "results": [[{"field": "@ptr", "value": "private"},
                                                     {"field": "level", "value": "ERROR"}]]}
        with patch.object(lab, "aws", side_effect=fake), patch("builtins.print") as out:
            lab.query(self.state, "errors")
        result = json.loads(out.call_args.args[0])
        self.assertEqual(result["rows"], [{"level": "ERROR"}])
        self.assertNotIn("stop-query", operations)


if __name__ == "__main__":
    unittest.main()
