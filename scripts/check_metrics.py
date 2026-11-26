"""Check MetricFlow against plain SQL on the same data. Needs `dbt build` first,
and the `mf` command line tool from requirements.txt.

A semantic layer only earns trust if its numbers match a query anyone can
read. So for a few metrics this:
  1. asks MetricFlow for the value (mf query --csv),
  2. computes the same value with plain SQL on dev.duckdb,
  3. prints both side by side, and fails if a pair is more than 0.01 apart.

It ends by showing why a ratio must be divided after summing: the average of
each region's refund rate is not the refund rate overall.

Run: python scripts/check_metrics.py
"""
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
TOLERANCE = 0.01      # MetricFlow rounds floats, so pairs are "about equal"
MF = shutil.which("mf") or str(Path(sys.executable).with_name("mf"))
pairs = []            # (metric, group, plain SQL value, MetricFlow value)
errors = []


def mf_query(metrics, group_by=None):
    """MetricFlow's rows for one query, as {group: {column: value}}. Values are text.
    MetricFlow runs first and on its own: DuckDB allows one process to write dev.duckdb."""
    args = [MF, "query", "--metrics", metrics, "--quiet"]
    if group_by:
        args += ["--group-by", group_by]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "result.csv"
        run = subprocess.run([*args, "--csv", str(path)], cwd=ROOT, capture_output=True, text=True)
        if run.returncode != 0:
            sys.exit(f"mf query {metrics} failed:\n{run.stderr.strip()}")
        with path.open(newline="", encoding="utf-8") as handle:
            # [:10] turns a month like 2026-09-01T00:00:00 into 2026-09-01
            return {row.pop(group_by, "all")[:10]: row for row in csv.DictReader(handle)}


def compare(metric, sql_values, mf_values):
    """Pair up the two answers. Both map a group to a value. A group one side lacks is None."""
    for group in sorted(set(sql_values) | set(mf_values)):
        value = mf_values.get(group)
        pairs.append((metric, group, sql_values.get(group), None if value in (None, "") else float(value)))


def show(value):
    """A number for the table, such as 5, 560 or 0.125. Blank for None."""
    return "" if value is None else f"{value:.4f}".rstrip("0").rstrip(".")


# MetricFlow reads target/semantic_manifest.json, not the YAML. Re-parse first,
# or a metric edited since the last dbt run is checked in its old form.
DBT = shutil.which("dbt") or str(Path(sys.executable).with_name("dbt"))
if subprocess.run([DBT, "parse", "--quiet"], cwd=ROOT).returncode != 0:
    sys.exit("dbt parse failed")

# MetricFlow first, then plain SQL on the tables dbt built.
overall = mf_query("refund_rate")["all"]
by_region = mf_query("refund_rate", "deal__region")
by_month = mf_query("deals,net_revenue", "metric_time__month")

rate = "sum(refund_gbp) / sum(fee_gbp)"
con = duckdb.connect(str(ROOT / "dev.duckdb"), read_only=True)
overall_sql = con.execute(f"select {rate} from marts.fct_deals").fetchone()[0]
region_sql = dict(con.execute(f"select region, {rate} from marts.fct_deals group by region").fetchall())
month_sql = con.execute(
    "select strftime(date_trunc('month', sold_at), '%Y-%m-%d'), count(*), sum(net_revenue_gbp)"
    " from marts.fct_deals group by 1"
).fetchall()
con.close()

compare("refund_rate", {"all": overall_sql}, {"all": overall["refund_rate"]})
compare("refund_rate", region_sql, {g: row["refund_rate"] for g, row in by_region.items()})
compare("deals", {m[0]: m[1] for m in month_sql}, {g: row["deals"] for g, row in by_month.items()})
compare("net_revenue", {m[0]: m[2] for m in month_sql}, {g: row["net_revenue"] for g, row in by_month.items()})

print(f"{'metric':<13}{'group':<16}{'plain sql':>12}{'metricflow':>14}{'difference':>14}")
for metric, group, sql_value, mf_value in pairs:
    difference = None if sql_value is None or mf_value is None else mf_value - sql_value
    print(f"{metric:<13}{group:<16}{show(sql_value):>12}{show(mf_value):>14}{show(difference):>14}")
    if difference is None or abs(difference) > TOLERANCE:
        errors.append(f"{metric} for {group}: plain SQL {show(sql_value)}, MetricFlow {show(mf_value)}")

rates = [float(row["refund_rate"]) for row in by_region.values() if row["refund_rate"] != ""]
print()
print(f"average of the region refund rates: {show(sum(rates) / len(rates))}")
print(f"true refund rate (sum of refunds / sum of fees): {show(overall_sql)}")

for error in errors:
    print("FAIL", error)
print(f"{len(pairs)} values compared, {len(errors)} problem(s)")
sys.exit(1 if errors else 0)
