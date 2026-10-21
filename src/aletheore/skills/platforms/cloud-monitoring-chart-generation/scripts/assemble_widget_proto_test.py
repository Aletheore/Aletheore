"""Unit tests for assemble_widget_proto.py."""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from absl.testing import absltest
import assemble_widget_proto  # type: ignore[missing-import]


class AssembleWidgetProtoTest(absltest.TestCase):

  def test_assemble_widget_textproto_basic(self):
    proto_text = assemble_widget_proto.assemble_widget_textproto(
        title="VM CPU Utilization",
        promql_query='rate(compute_googleapis_com:instance_cpu_utilization[1m])',
        plot_type="LINE",
        y_axis_label="Utilization (%)",
        unit_override="%",
    )
    self.assertIn('title: "VM CPU Utilization"', proto_text)
    self.assertIn('prometheus_query: "rate(compute_googleapis_com:instance_cpu_utilization[1m])"', proto_text)
    self.assertIn('unit_override: "%"', proto_text)
    self.assertIn("plot_type: LINE", proto_text)
    self.assertIn('label: "Utilization (%)"', proto_text)
    self.assertNotIn("legend_template:", proto_text)

  def test_assemble_widget_textproto_stacked_area(self):
    proto_text = assemble_widget_proto.assemble_widget_textproto(
        title="Hosts by Region",
        promql_query='count by (region) (requests[2m])',
        plot_type="STACKED_AREA",
    )
    self.assertIn("plot_type: STACKED_AREA", proto_text)
    self.assertIn("target_axis: Y1", proto_text)
    self.assertNotIn("legend_template:", proto_text)

  def test_get_auto_output_path(self):
    tmp_dir = self.create_tempdir().full_path
    path = assemble_widget_proto.get_auto_output_path(tmp_dir)
    self.assertTrue(os.path.basename(path).startswith("chart_"))
    self.assertTrue(path.endswith(".textproto"))


if __name__ == "__main__":
  absltest.main()
