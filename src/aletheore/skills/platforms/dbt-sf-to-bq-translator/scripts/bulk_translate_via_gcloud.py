# Copyright 2026 Google. This software is provided as-is, without warranty or
# representation for any use or purpose. Your use of it is subject to your
# agreement with Google.

# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///

import argparse
import re

from dbt_translator import run_bulk_translation


def main():
    parser = argparse.ArgumentParser(
        description="Bulk translate Snowflake dbt models to BigQuery using BQMS."
    )
    parser.add_argument(
        "--input",
        "--input-dir",
        dest="input",
        required=True,
        help="Input directory containing Snowflake SQL files",
    )
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output",
        required=True,
        help="Output directory for BigQuery SQL files",
    )
    parser.add_argument(
        "--bucket",
        "--gcs-bucket",
        dest="bucket",
        required=True,
        help="GCS Bucket for staging files",
    )
    parser.add_argument(
        "--location", default="us", help="GCP Region for BQMS (default: us)"
    )
    parser.add_argument(
        "--metadata",
        "--metadata-path",
        dest="metadata",
        help="Local path to metadata ZIP (deprecated in BQMS v2)",
    )
    parser.add_argument(
        "--metadata-dataset",
        help="BigQuery dataset containing migration assessment metadata",
    )
    parser.add_argument(
        "--default-database", help="Default Snowflake database for object resolution"
    )
    parser.add_argument(
        "--schema-search-path",
        help="Comma-separated list of default schemas for object resolution",
    )
    parser.add_argument(
        "--array-cast-mode",
        choices=["string", "array_string", "json_extract_string_array", "preserve"],
        default=None,
        help="How to cast untyped ARRAY<JSON> columns: 'string' -> CAST(col AS STRING), 'array_string' -> CAST(col AS ARRAY<STRING>), 'json_extract_string_array' -> JSON_EXTRACT_STRING_ARRAY(col), 'preserve' -> leave as ARRAY<JSON>",
    )
    parser.add_argument(
        "--struct-columns",
        default=None,
        help="Comma-separated list of additional STRUCT column names (e.g. 'struct_col,record_col') to convert from json_value to dot notation.",
    )

    args = parser.parse_args()

    bucket_clean = re.sub(r"^gs://", "", args.bucket)

    schema_search_list = None
    if args.schema_search_path:
        schema_search_list = [s.strip() for s in args.schema_search_path.split(",")]

    struct_columns_list = None
    if args.struct_columns:
        struct_columns_list = [s.strip() for s in args.struct_columns.split(",")]

    msg = run_bulk_translation(
        args.input,
        args.output,
        bucket_clean,
        args.location,
        metadata_path=args.metadata,
        metadata_dataset=args.metadata_dataset,
        default_database=args.default_database,
        schema_search_path=schema_search_list,
        array_cast_mode=args.array_cast_mode,
        struct_columns=struct_columns_list,
    )
    print(msg)


if __name__ == "__main__":
    main()
