#!/usr/bin/env python3
"""Unit tests and stress tests for validate_promql.py."""

import unittest
import validate_promql


class ValidatePromqlTest(unittest.TestCase):

  # --- Valid PromQL Query Tests ---

  def test_valid_counter_query(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_increase_counter_query(self):
    query = (
        'sum(increase(spanner_googleapis_com:api_request_count'
        '{monitored_resource="spanner_instance"}[1h]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_irate_counter_query(self):
    query = (
        'sum(irate(loadbalancing_googleapis_com:https_request_count'
        '{monitored_resource="https_lb_rule"}[5m]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_histogram_query(self):
    query = (
        'histogram_quantile(0.95,'
        ' sum(rate(spanner_googleapis_com:query_latency_bucket'
        '{monitored_resource="spanner_instance"}[5m])) by (le, instance_id))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_binary_expression(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m])) /'
        ' sum(rate(compute_googleapis_com:instance_disk_write_bytes_count'
        '{monitored_resource="gce_instance"}[5m]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_subquery(self):
    query = (
        'rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m])[30m:1m]'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_boolean_query(self):
    query = (
        'pubsub_googleapis_com:subscription_delivery_latency_health_score{monitored_resource="pubsub_subscription",'
        ' subscription_id="sub-1"} == 0'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_topk_aggregation(self):
    query = (
        'topk(10,'
        ' avg_over_time(agent_googleapis_com:memory_percent_used'
        '{monitored_resource="gce_instance", state!="free"}[5m]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_bottomk_aggregation(self):
    query = (
        'bottomk(5,'
        ' sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m])) by (instance_id))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_regex_label_matcher(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_cpu_utilization_count'
        '{monitored_resource="gce_instance", instance_name=~"web-.*"}[5m]))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_offset_modifier(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m] offset 1d))'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  def test_valid_group_left_matching(self):
    query = (
        'sum(rate(error_count{monitored_resource="k8s_container"}[5m])) by'
        ' (method, service) / on (service) group_left(method)'
        ' sum(rate(total_requests{monitored_resource="k8s_container"}[5m]))'
        ' by (service)'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  # --- Invalid & Edge Case Tests ---

  def test_missing_monitored_resource(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{instance_id="123"}[5m]))'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('monitored_resource' in err for err in errors),
        f'Expected monitored_resource error, got: {errors}',
    )

  def test_counter_not_wrapped(self):
    query = (
        'compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}'
    )
    errors, warnings = validate_promql.validate_promql_query(query)
    self.assertFalse(errors, f'Expected no errors, got: {errors}')
    self.assertTrue(
        any('wrapped in rate() or increase()' in warn for warn in warnings),
        f'Expected counter wrapping warning, got: {warnings}',
    )

  def test_histogram_bucket_not_wrapped(self):
    query = (
        'spanner_googleapis_com:query_latency_bucket'
        '{monitored_resource="spanner_instance"}'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('wrapped in histogram_quantile()' in err for err in errors),
        f'Expected histogram wrapping error, got: {errors}',
    )

  def test_invalid_grouping_clause(self):
    query = (
        'compute_googleapis_com:instance_cpu_utilization'
        '{monitored_resource="gce_instance"} by (instance_id)'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('Syntax Error' in err for err in errors),
        f'Expected syntax error, got: {errors}',
    )

  def test_unclosed_bracket_syntax_error(self):
    query = (
        'compute_googleapis_com:instance_cpu_utilization'
        '{monitored_resource="gce_instance"'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('Syntax Error' in err for err in errors),
        f'Expected syntax error, got: {errors}',
    )

  def test_multiple_selectors_one_missing_monitored_resource(self):
    query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m])) + '
        'sum(rate(compute_googleapis_com:instance_disk_write_bytes_count[5m]))'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('monitored_resource' in err for err in errors),
        f'Expected monitored_resource error, got: {errors}',
    )

  def test_state_filtered_metric_missing_state_filter(self):
    # Test memory metric missing state filter
    query = (
        'avg(agent_googleapis_com:memory_percent_used'
        '{monitored_resource="gce_instance"})'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('missing the \'state!="free"\'' in err for err in errors),
        f'Expected state filter missing error, got: {errors}',
    )

    # Test disk metric missing state filter
    query = (
        'avg(agent_googleapis_com:disk_percent_used'
        '{monitored_resource="gce_instance"})'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('missing the \'state!="free"\'' in err for err in errors),
        f'Expected state filter missing error, got: {errors}',
    )

  def test_state_filtered_metric_using_state_used(self):
    # Test memory metric using state="used"
    query = (
        'avg(agent_googleapis_com:memory_percent_used'
        '{monitored_resource="gce_instance", state="used"})'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('must not use \'state="used"\'' in err for err in errors),
        f'Expected state="used" error, got: {errors}',
    )

    # Test disk metric using state="used"
    query = (
        'avg(agent_googleapis_com:disk_percent_used'
        '{monitored_resource="gce_instance", state="used"})'
    )
    errors, _ = validate_promql.validate_promql_query(query)
    self.assertTrue(
        any('must not use \'state="used"\'' in err for err in errors),
        f'Expected state="used" error, got: {errors}',
    )

  def test_state_filtered_metric_valid_disk(self):
    query = (
        'avg(agent_googleapis_com:disk_percent_used'
        '{monitored_resource="gce_instance", state!="free"})'
    )
    self.assertEqual(validate_promql.validate_promql_query(query), ([], []))

  # --- Query Extractor Markdown & Macro Parsing Tests ---

  def test_extract_query(self):
    markdown = (
        'Here is the query:\n'
        '```promql\n'
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m]))\n'
        '```\n'
        'Hope this helps!'
    )
    expected = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m]))'
    )
    self.assertEqual(validate_promql.extract_query(markdown), expected)

  def test_extract_query_with_interval_macro(self):
    raw_query = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[${__interval}]))'
    )
    expected = (
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count'
        '{monitored_resource="gce_instance"}[5m]))'
    )
    extracted = validate_promql.extract_query(raw_query)
    self.assertEqual(extracted, expected)
    self.assertEqual(validate_promql.validate_promql_query(extracted), ([], []))

  def test_multi_query_validation(self):
    queries = [
        'compute_googleapis_com:instance_cpu_utilization{monitored_resource="gce_instance"}',
        'sum(rate(compute_googleapis_com:instance_disk_read_bytes_count{monitored_resource="gce_instance"}[5m]))',
    ]
    for q in queries:
      self.assertEqual(validate_promql.validate_promql_query(q), ([], []))


if __name__ == '__main__':
  unittest.main()
