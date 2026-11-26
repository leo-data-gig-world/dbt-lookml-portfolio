-- Grain: one row per day.
-- MetricFlow joins cumulative metrics and offsets to this spine, so it has to
-- cover every date in the data. The seeds hold dates from 2026-01-05 to
-- 2026-10-01. The range is fixed, so widen it if new data falls outside 2026.
-- generate_series is DuckDB syntax, and it includes both end dates.
select cast(date_day as date) as date_day
from generate_series(
    date '2026-01-01',
    date '2026-12-31',
    interval 1 day
) as days(date_day)
