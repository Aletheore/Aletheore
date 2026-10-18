"""Lightweight grammar-based textproto parser and SDUI Widget models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Aggregation:
  alignment_period: dict = field(default_factory=dict)
  per_series_aligner: str = ""
  cross_series_reducer: str = ""
  group_by_fields: list[str] = field(default_factory=list)


@dataclass
class TimeSeriesFilter:
  filter: str = ""
  aggregation: Aggregation | None = None


@dataclass
class TimeSeriesQuery:
  prometheus_query: str = ""
  time_series_filter: TimeSeriesFilter | None = None
  unit_override: str = ""

@dataclass
class DataSet:
  time_series_query: TimeSeriesQuery = field(default_factory=TimeSeriesQuery)
  plot_type: str = "LINE"
  target_axis: str = "Y1"

@dataclass
class YAxis:
  label: str = ""
  scale: str = "LINEAR"

@dataclass
class ChartOptions:
  mode: str = "COLOR"

@dataclass
class XyChart:
  chart_options: ChartOptions = field(default_factory=ChartOptions)
  data_sets: list[DataSet] = field(default_factory=list)
  y_axis: YAxis = field(default_factory=YAxis)

@dataclass
class Widget:
  title: str = ""
  xy_chart: XyChart | None = None

  def HasField(self, field_name: str) -> bool:
    return getattr(self, field_name, None) is not None

def tokenize_textproto(text: str) -> list[tuple[str, str]]:
  """Tokenizes textproto string into (token_type, value) tuples."""
  tokens = []
  i = 0
  n = len(text)
  while i < n:
    c = text[i]
    if c.isspace():
      i += 1
      continue
    if c == "#":
      while i < n and text[i] != "\n":
        i += 1
      continue
    if c in ("{", "}", ":", ";", ","):
      if c in ("{", "}", ":"):
        tokens.append((c, c))
      i += 1
      continue
    if c in ('"', "'"):
      quote = c
      j = i + 1
      val_chars = []
      while j < n and text[j] != quote:
        if text[j] == "\\" and j + 1 < n:
          val_chars.append(text[j + 1])
          j += 2
        else:
          val_chars.append(text[j])
          j += 1
      tokens.append(("STR", "".join(val_chars)))
      i = j + 1
      continue
    j = i
    while (
        j < n
        and not text[j].isspace()
        and text[j] not in ("{", "}", ":", ";", ",", "#")
    ):
      j += 1
    tokens.append(("SYM", text[i:j]))
    i = j
  return tokens

def parse_textproto_tokens(
    tokens: list[tuple[str, str]], idx: int = 0
) -> tuple[dict[str, Any], int]:
  """Parses token stream into a nested dictionary structure similar to json.loads."""
  obj: dict[str, Any] = {}
  n = len(tokens)
  while idx < n:
    ttype, tval = tokens[idx]
    if ttype == "}":
      return obj, idx + 1
    if ttype == "SYM":
      key = tval
      idx += 1
      if idx < n and tokens[idx][0] == ":":
        idx += 1
      if idx < n and tokens[idx][0] == "{":
        nested_obj, idx = parse_textproto_tokens(tokens, idx + 1)
        val = nested_obj
      elif idx < n and tokens[idx][0] in ("STR", "SYM"):
        val = tokens[idx][1]
        idx += 1
      else:
        continue

      if key in obj:
        if not isinstance(obj[key], list):
          obj[key] = [obj[key]]
        obj[key].append(val)
      else:
        obj[key] = val
    else:
      idx += 1
  return obj, idx

def dict_to_widget(data: dict[str, Any]) -> Widget:
  """Converts a parsed textproto dictionary into a structured Widget hierarchy, enforcing strict field boundaries."""
  if "widget" in data and isinstance(data["widget"], dict):
    # Only allow "widget" or expected fields at the top level
    if set(data.keys()) - {"widget"} != set():
      bad_keys = set(data.keys()) - {"widget"}
      raise ValueError(f"Unknown properties at top level: {bad_keys}")
    data = data["widget"]

  expected_widget_keys = {"title", "xy_chart"}
  bad_widget_keys = set(data.keys()) - expected_widget_keys
  if bad_widget_keys:
    raise ValueError(f"Unknown properties in Widget: {bad_widget_keys}")

  title = str(data.get("title", ""))

  xy_data = data.get("xy_chart")
  if not xy_data or not isinstance(xy_data, dict):
    return Widget(title=title, xy_chart=None)

  expected_xy_keys = {"chart_options", "data_sets", "y_axis"}
  bad_xy_keys = set(xy_data.keys()) - expected_xy_keys
  if bad_xy_keys:
    raise ValueError(f"Unknown properties in xy_chart: {bad_xy_keys}")

  opts_data = xy_data.get("chart_options", {})
  if isinstance(opts_data, dict):
    bad_opts_keys = set(opts_data.keys()) - {"mode"}
    if bad_opts_keys:
      raise ValueError(f"Unknown properties in chart_options: {bad_opts_keys}")
  
  mode = (
      str(opts_data.get("mode", "COLOR"))
      if isinstance(opts_data, dict)
      else "COLOR"
  )
  chart_options = ChartOptions(mode=mode)

  yaxis_data = xy_data.get("y_axis", {})
  if isinstance(yaxis_data, dict):
    bad_yaxis_keys = set(yaxis_data.keys()) - {"label", "scale"}
    if bad_yaxis_keys:
      raise ValueError(f"Unknown properties in y_axis: {bad_yaxis_keys}")
    label = str(yaxis_data.get("label", ""))
    scale = str(yaxis_data.get("scale", "LINEAR"))
  else:
    label, scale = "", "LINEAR"
  y_axis = YAxis(label=label, scale=scale)

  raw_ds_list = xy_data.get("data_sets", [])
  if isinstance(raw_ds_list, dict):
    raw_ds_list = [raw_ds_list]
  elif not isinstance(raw_ds_list, list):
    raw_ds_list = []

  data_sets: list[DataSet] = []
  for ds_dict in raw_ds_list:
    if not isinstance(ds_dict, dict):
      continue
    
    bad_ds_keys = set(ds_dict.keys()) - {"time_series_query", "plot_type", "target_axis"}
    if bad_ds_keys:
      raise ValueError(f"Unknown properties in data_sets: {bad_ds_keys}")

    ts_dict = ds_dict.get("time_series_query", {})
    ts_filter = None
    if isinstance(ts_dict, dict):
      bad_ts_keys = set(ts_dict.keys()) - {"prometheus_query", "time_series_filter", "unit_override"}
      if bad_ts_keys:
        raise ValueError(f"Unknown properties in time_series_query: {bad_ts_keys}")
      
      promql = str(ts_dict.get("prometheus_query", ""))
      unit = str(ts_dict.get("unit_override", ""))
      ts_filter_dict = ts_dict.get("time_series_filter")
      if isinstance(ts_filter_dict, dict):
        bad_lts_keys = set(ts_filter_dict.keys()) - {"filter", "aggregation"}
        if bad_lts_keys:
          raise ValueError(f"Unknown properties in time_series_filter: {bad_lts_keys}")

        agg_dict = ts_filter_dict.get("aggregation", {})
        agg_obj = None
        if isinstance(agg_dict, dict) and agg_dict:
          bad_agg_keys = set(agg_dict.keys()) - {"alignment_period", "per_series_aligner", "cross_series_reducer", "group_by_fields"}
          if bad_agg_keys:
            raise ValueError(f"Unknown properties in aggregation: {bad_agg_keys}")

          group_by = agg_dict.get("group_by_fields", [])
          if isinstance(group_by, str):
            group_by = [group_by]
          agg_obj = Aggregation(
              alignment_period=agg_dict.get("alignment_period", {}),
              per_series_aligner=str(agg_dict.get("per_series_aligner", "")),
              cross_series_reducer=str(
                  agg_dict.get("cross_series_reducer", "")
              ),
              group_by_fields=group_by,
          )
        ts_filter = TimeSeriesFilter(
            filter=str(ts_filter_dict.get("filter", "")), aggregation=agg_obj
        )
    else:
      promql, unit = "", ""
    ts_query = TimeSeriesQuery(
        prometheus_query=promql,
        time_series_filter=ts_filter,
        unit_override=unit,
    )
    plot_type = str(ds_dict.get("plot_type", "LINE"))
    target_axis = str(ds_dict.get("target_axis", "Y1"))
    data_sets.append(
        DataSet(
            time_series_query=ts_query,
            plot_type=plot_type,
            target_axis=target_axis,
        )
    )

  xy_chart = XyChart(
      chart_options=chart_options,
      data_sets=data_sets,
      y_axis=y_axis,
  )
  return Widget(title=title, xy_chart=xy_chart)

def parse_and_validate_widget(text: str) -> Widget:
  """Parses a textproto string into a Widget message hierarchy."""
  tokens = tokenize_textproto(text)
  raw_dict, _ = parse_textproto_tokens(tokens)
  return dict_to_widget(raw_dict)
