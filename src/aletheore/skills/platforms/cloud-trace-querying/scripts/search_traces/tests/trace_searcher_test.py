import hashlib
import sys
import unittest
from unittest import mock

# Mock trace_v1 dependencies by directly injecting into sys.modules
mock_trace = mock.MagicMock()
mock_trace_v1 = mock.MagicMock()
mock_trace.trace_v1 = mock_trace_v1
sys.modules["google.cloud"] = mock_trace
sys.modules["google.cloud.trace_v1"] = mock_trace_v1

import timestamp_utils
import trace_searcher


class TestTraceSearcher(unittest.TestCase):

  def test_get_trace_client_caching(self):
    # Verify client is cached
    with mock.patch.object(
        trace_searcher.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client_class.return_value = mock.MagicMock()
      # Reset local global cache first
      trace_searcher._trace_client = None
      client1 = trace_searcher._get_trace_client()
      client2 = trace_searcher._get_trace_client()
      self.assertIs(client1, client2)
      mock_client_class.assert_called_once()

  def test_time_range_validation(self):
    # Verify difference <= 7 days is OK, > 7 days raises ValueError
    with mock.patch.object(
        trace_searcher, "_get_trace_client"
    ) as mock_get_client:
      mock_client = mock.MagicMock()
      mock_get_client.return_value = mock_client
      mock_client.list_traces.return_value = []

      # OK: exactly 7 days
      trace_searcher.search_traces(
          projects=["p"],
          filter_query="*",
          limit=1,
          start_time_str="2026-06-25T13:00:00Z",
          end_time_str="2026-07-02T13:00:00Z",
      )

      # Fails: 7 days and 1 second
      with self.assertRaises(ValueError) as ctx:
        trace_searcher.search_traces(
            projects=["p"],
            filter_query="*",
            limit=1,
            start_time_str="2026-06-25T13:00:00Z",
            end_time_str="2026-07-02T13:00:01Z",
        )
      self.assertIn("cannot exceed 7 days", str(ctx.exception))

  def test_get_service_identifiers(self):
    # 1. OTel with namespace
    span1 = {"labels": {"service.name": "my-svc", "service.namespace": "my-ns"}}
    self.assertEqual(
        trace_searcher._get_service_identifiers(span1),
        {"otel_service_name": "my-svc", "otel_service_namespace": "my-ns"},
    )

    # 2. OTel without namespace
    span2 = {"labels": {"service.name": "my-svc"}}
    self.assertEqual(
        trace_searcher._get_service_identifiers(span2),
        {"otel_service_name": "my-svc"},
    )

    # 3. GcpAppHub
    span3 = {
        "labels": {
            "gcp.apphub.service.id": "hub-svc",
            "gcp.apphub.workload.id": "hub-workload",
        }
    }
    self.assertEqual(
        trace_searcher._get_service_identifiers(span3),
        {"apphub_service_id": "hub-svc", "apphub_workload_id": "hub-workload"},
    )

    # 4. GcpMcpServer
    span4 = {"labels": {"gcp.mcp.server.id": "mcp-svc"}}
    self.assertEqual(
        trace_searcher._get_service_identifiers(span4),
        {"gcp_mcp_server_id": "mcp-svc"},
    )

    # 5. Fallback/Empty
    span5 = {"labels": {"some-other-label": "val"}}
    self.assertEqual(trace_searcher._get_service_identifiers(span5), {})

  def test_get_canonical_service_name(self):
    self.assertEqual(
        trace_searcher._get_canonical_service_name({
            "otel_service_name": "my-svc",
            "otel_service_namespace": "my-ns",
        }),
        "my-ns/my-svc",
    )
    self.assertEqual(
        trace_searcher._get_canonical_service_name(
            {"otel_service_name": "my-svc"}
        ),
        "my-svc",
    )
    self.assertEqual(
        trace_searcher._get_canonical_service_name(
            {"apphub_service_id": "hub-svc"}
        ),
        "hub-svc",
    )
    self.assertEqual(
        trace_searcher._get_canonical_service_name(
            {"gcp_mcp_server_id": "mcp-svc"}
        ),
        "mcp-svc",
    )
    self.assertIsNone(trace_searcher._get_canonical_service_name({}))

  def test_distill_trace_all_features(self):
    trace = {
        "traceId": "trace-999",
        "spans": [
            {
                "spanId": "1",
                "startTime": "2026-06-25T13:00:00.100Z",
                "endTime": "2026-06-25T13:00:01.200Z",
                "labels": {
                    "/http/status_code": "200",
                    "env": "prod",
                    "service.name": "service-a",
                },
            },
            {
                "spanId": "2",
                "parentSpanId": "1",
                "startTime": "2026-06-25T13:00:00.200Z",
                "endTime": "2026-06-25T13:00:00.800Z",
                "labels": {
                    "db.type": "mysql",
                    "gcp.apphub.service.id": "service-b",
                },
            },
            {
                "spanId": "3",
                "parentSpanId": "1",
                "startTime": "2026-06-25T13:00:00.500Z",
                "endTime": "2026-06-25T13:00:01.500Z",
                "status": {"code": 13, "message": "Failed"},
                "labels": {
                    "foo": "bar",
                    "gcp.mcp.server.id": "service-c",
                },
            },
            {
                "spanId": "4",
                "startTime": "2026-06-25T13:00:00.300Z",
                "endTime": "2026-06-25T13:00:00.400Z",
                "labels": {
                    "/http/status_code": "500",
                    "service.name": "service-a",
                },
            },
        ],
    }

    distilled_example = trace_searcher.distill_trace(trace)
    self.assertEqual(distilled_example.trace_id, "trace-999")
    self.assertEqual(
        distilled_example.earliest_timestamp, "2026-06-25T13:00:00.100000Z"
    )
    self.assertEqual(
        distilled_example.last_timestamp, "2026-06-25T13:00:01.500000Z"
    )
    self.assertAlmostEqual(distilled_example.duration_seconds, 1.4)
    self.assertEqual(distilled_example.num_spans, 4)
    self.assertEqual(
        distilled_example.unique_services,
        ["service-a", "service-b", "service-c"],
    )
    self.assertEqual(
        distilled_example.unique_attribute_keys,
        [
            "/http/status_code",
            "db.type",
            "env",
            "foo",
            "gcp.apphub.service.id",
            "gcp.mcp.server.id",
            "service.name",
        ],
    )
    self.assertAlmostEqual(
        distilled_example.longest_non_root_span_duration_seconds, 1.0
    )
    self.assertEqual(distilled_example.num_error_spans, 2)

    # Verify error details inlined structure
    self.assertEqual(len(distilled_example.errors), 2)
    err1 = distilled_example.errors[0]
    self.assertEqual(err1["span_id"], "3")
    self.assertEqual(err1["service"], "service-c")
    self.assertEqual(
        err1["error_signifying_labels"],
        {"status": {"code": 13, "message": "Failed"}},
    )

    # test leaner to_dict serialization (omit unique_services, num_spans, num_error_spans, unique_attribute_keys)
    dist_dict = distilled_example.to_dict()
    self.assertEqual(dist_dict["trace_id"], "trace-999")
    self.assertNotIn("unique_services", dist_dict)
    self.assertNotIn("num_spans", dist_dict)
    self.assertNotIn("num_error_spans", dist_dict)
    self.assertNotIn("unique_attribute_keys", dist_dict)
    self.assertIn("errors", dist_dict)

  def test_clustering_and_sorting_rules_updated(self):
    # We want to test sorting rules and cluster_id:
    # 1. Grouped by service, num_spans, num_error_spans
    # 2. Clusters containing traces with errors are ranked higher (sorted by num_error_spans descending)
    # 3. Then sort by maximum latency/duration descending
    # 4. Finally sort by total count of traces descending

    with mock.patch.object(
        trace_searcher, "_get_trace_client"
    ) as mock_get_client:
      mock_client = mock.MagicMock()
      mock_get_client.return_value = mock_client

      t1 = {
          "traceId": "t1",
          "spans": [{
              "startTime": "2026-06-25T13:00:00Z",
              "endTime": "2026-06-25T13:00:10Z",  # 10s
              "labels": {"service.name": "A"},
          }],
      }
      t2 = {
          "traceId": "t2",
          "spans": [{
              "startTime": "2026-06-25T13:00:00Z",
              "endTime": "2026-06-25T13:00:05Z",  # 5s
              "labels": {"service.name": "B"},
              "status": {"code": 1},  # error
          }],
      }
      t3a = {
          "traceId": "t3a",
          "spans": [
              {
                  "startTime": "2026-06-25T13:00:00Z",
                  "endTime": "2026-06-25T13:00:02Z",  # 2s
                  "labels": {"service.name": "C"},
                  "status": {"code": 1},  # error
              },
              {
                  "startTime": "2026-06-25T13:00:00Z",
                  "endTime": "2026-06-25T13:00:01Z",
                  "labels": {"service.name": "C"},
                  "status": {"code": 2},  # error (total 2 errors, 2 spans)
              },
          ],
      }
      t3b = {
          "traceId": "t3b",
          "spans": [{
              "startTime": "2026-06-25T13:00:00Z",
              "endTime": "2026-06-25T13:00:01Z",  # 1s
              "labels": {"service.name": "C"},  # 1 span, 0 errors
          }],
      }
      t4 = {
          "traceId": "t4",
          "spans": [{
              "startTime": "2026-06-25T13:00:00Z",
              "endTime": "2026-06-25T13:00:20Z",  # 20s
              "labels": {"service.name": "D"},
          }],
      }

      mock_traces = [mock.MagicMock() for _ in range(5)]
      for i, pb in enumerate(mock_traces):
        pb._pb = mock.MagicMock()
      mock_client.list_traces.return_value = mock_traces

      with mock.patch.object(trace_searcher, "MessageToDict") as mock_to_dict:
        mock_to_dict.side_effect = [t1, t2, t3a, t3b, t4]

        result = trace_searcher.search_traces(
            projects=["test-proj"],
            filter_query="*",
            limit=5,
            examples_per_cluster=1,
        )

        clusters = result["clusters"]
        self.assertEqual(len(clusters), 5)

        # Expected sorting order:
        # 1. Cluster C (spans=2, errors=2) -> max error spans = 2
        # 2. Cluster B (spans=1, errors=1) -> max error spans = 1
        # 3. Cluster D (spans=1, errors=0, duration=20s) -> max error spans = 0
        # 4. Cluster A (spans=1, errors=0, duration=10s) -> max error spans = 0
        # 5. Cluster C (spans=1, errors=0, duration=1s) -> max error spans = 0

        # Cluster C (spans=2, errors=2)
        c0 = clusters[0]
        self.assertEqual(c0["services"], ["C"])
        self.assertEqual(c0["unique_attribute_keys"], ["service.name"])
        self.assertEqual(c0["num_spans"], 2)
        self.assertEqual(c0["num_error_spans"], 2)
        self.assertEqual(c0["total_matching_traces"], 1)
        self.assertEqual(c0["matching_trace_ids"], ["t3a"])
        self.assertNotIn("unique_attribute_keys", c0["examples"][0])
        expected_cid_c2 = hashlib.md5(
            b"services=C;num_spans=2;num_error_spans=2"
        ).hexdigest()
        self.assertEqual(c0["cluster_id"], expected_cid_c2)

        # Cluster B
        c1 = clusters[1]
        self.assertEqual(c1["services"], ["B"])
        self.assertEqual(c1["unique_attribute_keys"], ["service.name"])
        self.assertEqual(c1["num_spans"], 1)
        self.assertEqual(c1["num_error_spans"], 1)
        self.assertEqual(c1["matching_trace_ids"], ["t2"])
        self.assertNotIn("unique_attribute_keys", c1["examples"][0])
        expected_cid_b = hashlib.md5(
            b"services=B;num_spans=1;num_error_spans=1"
        ).hexdigest()
        self.assertEqual(c1["cluster_id"], expected_cid_b)

        # Cluster D
        c2 = clusters[2]
        self.assertEqual(c2["services"], ["D"])
        self.assertEqual(c2["unique_attribute_keys"], ["service.name"])
        self.assertEqual(c2["num_spans"], 1)
        self.assertEqual(c2["num_error_spans"], 0)
        self.assertEqual(c2["matching_trace_ids"], ["t4"])
        self.assertNotIn("unique_attribute_keys", c2["examples"][0])
        expected_cid_d = hashlib.md5(
            b"services=D;num_spans=1;num_error_spans=0"
        ).hexdigest()
        self.assertEqual(c2["cluster_id"], expected_cid_d)

        # Cluster A
        c3 = clusters[3]
        self.assertEqual(c3["services"], ["A"])
        self.assertEqual(c3["unique_attribute_keys"], ["service.name"])
        self.assertEqual(c3["num_spans"], 1)
        self.assertEqual(c3["num_error_spans"], 0)
        self.assertEqual(c3["matching_trace_ids"], ["t1"])
        self.assertNotIn("unique_attribute_keys", c3["examples"][0])
        expected_cid_a = hashlib.md5(
            b"services=A;num_spans=1;num_error_spans=0"
        ).hexdigest()
        self.assertEqual(c3["cluster_id"], expected_cid_a)

        # Cluster C (spans=1, errors=0)
        c4 = clusters[4]
        self.assertEqual(c4["services"], ["C"])
        self.assertEqual(c4["unique_attribute_keys"], ["service.name"])
        self.assertEqual(c4["num_spans"], 1)
        self.assertEqual(c4["num_error_spans"], 0)
        self.assertEqual(c4["matching_trace_ids"], ["t3b"])
        self.assertNotIn("unique_attribute_keys", c4["examples"][0])
        expected_cid_c1 = hashlib.md5(
            b"services=C;num_spans=1;num_error_spans=0"
        ).hexdigest()
        self.assertEqual(c4["cluster_id"], expected_cid_c1)

  def test_is_error_span_otel_http(self):
    # OTel HTTP conventions: status in range [200, 399] is OK
    self.assertFalse(
        trace_searcher.is_error_span(
            {"attributes": {"http.response.status_code": 200}}
        )
    )
    self.assertFalse(
        trace_searcher.is_error_span(
            {"attributes": {"http.status_code": "302"}}
        )
    )
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"http.response.status_code": 404}}
        )
    )
    self.assertTrue(
        trace_searcher.is_error_span({"attributes": {"http.status_code": 500}})
    )
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"http.response.status_code": 199}}
        )
    )

    # Legacy GCP /http/status_code
    self.assertFalse(
        trace_searcher.is_error_span({"labels": {"/http/status_code": "200"}})
    )
    self.assertTrue(
        trace_searcher.is_error_span({"labels": {"/http/status_code": "400"}})
    )
    self.assertTrue(
        trace_searcher.is_error_span({"labels": {"/http/status_code": "500"}})
    )

  def test_is_error_span_otel_grpc(self):
    # OTel gRPC conventions
    self.assertFalse(
        trace_searcher.is_error_span(
            {"attributes": {"rpc.grpc.status_code": 0}}
        )
    )
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"rpc.grpc.status_code": 14}}
        )
    )

    # grpc.status
    self.assertFalse(
        trace_searcher.is_error_span({"attributes": {"grpc.status": "OK"}})
    )
    self.assertFalse(
        trace_searcher.is_error_span({"attributes": {"grpc.status": "0"}})
    )
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"grpc.status": "UNAVAILABLE"}}
        )
    )
    self.assertTrue(
        trace_searcher.is_error_span({"attributes": {"grpc.status": 12}})
    )

  def test_is_error_span_rpc_system(self):
    # rpc.system.name = grpc
    self.assertFalse(
        trace_searcher.is_error_span({
            "attributes": {
                "rpc.response.status_code": 0,
                "rpc.system.name": "grpc",
            }
        })
    )
    self.assertTrue(
        trace_searcher.is_error_span({
            "attributes": {
                "rpc.response.status_code": 14,
                "rpc.system.name": "grpc",
            }
        })
    )

    # rpc.system.name = http / web-related
    self.assertFalse(
        trace_searcher.is_error_span({
            "attributes": {
                "rpc.response.status_code": 200,
                "rpc.system.name": "http",
            }
        })
    )
    self.assertTrue(
        trace_searcher.is_error_span({
            "attributes": {
                "rpc.response.status_code": 404,
                "rpc.system.name": "http",
            }
        })
    )

  def test_is_error_span_generic_error_attributes(self):
    # error.type or error.message
    self.assertFalse(trace_searcher.is_error_span({"attributes": {}}))
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"error.type": "RuntimeError"}}
        )
    )
    self.assertTrue(
        trace_searcher.is_error_span(
            {"attributes": {"error.message": "Something failed"}}
        )
    )

  def test_is_error_span_top_level_status(self):
    # status as dictionary representing google.rpc.Status
    self.assertFalse(trace_searcher.is_error_span({"status": {"code": 0}}))
    self.assertTrue(
        trace_searcher.is_error_span(
            {"status": {"code": 3, "message": "Invalid argument"}}
        )
    )

    # status as string
    self.assertFalse(trace_searcher.is_error_span({"status": "OK"}))
    self.assertFalse(trace_searcher.is_error_span({"status": "0"}))
    self.assertTrue(trace_searcher.is_error_span({"status": "CANCELLED"}))

  def test_extract_error_labels_generalized(self):
    span = {
        "attributes": {
            "http.response.status_code": 500,
            "error.message": "Server Error",
            "some.other.attribute": "val",
        }
    }
    extracted = trace_searcher._extract_error_labels(span)
    self.assertEqual(extracted.get("http.response.status_code"), 500)
    self.assertEqual(extracted.get("error.message"), "Server Error")
    self.assertNotIn("some.other.attribute", extracted)


if __name__ == "__main__":
  unittest.main()
