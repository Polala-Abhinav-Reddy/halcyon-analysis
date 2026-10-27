import duckdb
from pathlib import Path

con = duckdb.connect()
data_dir = Path(__file__).resolve().parent / "data"
con.read_csv(str(data_dir / "deliveries.csv"), header=True, sample_size=-1).create_view("d")
con.read_csv(str(data_dir / "clients.csv"), header=True, sample_size=-1).create_view("cl")
con.read_csv(str(data_dir / "carriers.csv"), header=True, sample_size=-1).create_view("ca")

def show(title, sql):
    print("\n" + "="*78)
    print(title)
    print("="*78)
    print(con.sql(sql).df().to_string(index=False))

# -------- sanity --------
show("0a. Row count, date range, overall on-time", """
SELECT COUNT(*) AS rows,
       MIN(order_received_ts) AS first_order,
       MAX(order_received_ts) AS last_order,
       ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
       ROUND(AVG(days_late),2) AS avg_days_late
FROM d""")

show("0b. Null / blank counts per key column", """
SELECT
  SUM(CASE WHEN order_received_ts IS NULL THEN 1 ELSE 0 END) AS null_received,
  SUM(CASE WHEN pick_list_date   IS NULL THEN 1 ELSE 0 END) AS null_picklist,
  SUM(CASE WHEN shipped_ts       IS NULL THEN 1 ELSE 0 END) AS null_shipped,
  SUM(CASE WHEN delivered_date   IS NULL THEN 1 ELSE 0 END) AS null_delivered,
  SUM(CASE WHEN actual_route_km  IS NULL THEN 1 ELSE 0 END) AS null_actual_km,
  SUM(CASE WHEN days_late        IS NULL THEN 1 ELSE 0 END) AS null_days_late
FROM d""")

# -------- A: the four stages --------
show("A. Average days in each pipeline stage (ALL orders)", """
SELECT
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1_received_to_picklist,
  ROUND(AVG(date_diff('day', pick_list_date, shipped_ts::DATE)),3)        AS s2_picklist_to_shipped,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date)),3)        AS s3_shipped_to_delivered,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, delivered_date)),3) AS total_cycle_days
FROM d""")

show("A2. Same, split by on-time vs late", """
SELECT on_time,
  COUNT(*) AS n,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1,
  ROUND(AVG(date_diff('day', pick_list_date, shipped_ts::DATE)),3)        AS s2,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date)),3)        AS s3,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d GROUP BY 1 ORDER BY 1""")

show("A3. EXCESS days vs the on-time baseline (late orders only)", """
WITH base AS (
  SELECT AVG(date_diff('day', order_received_ts::DATE, pick_list_date)) AS b1,
         AVG(date_diff('day', pick_list_date, shipped_ts::DATE))        AS b2,
         AVG(date_diff('day', shipped_ts::DATE, delivered_date))        AS b3
  FROM d WHERE on_time='Y'
)
SELECT
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)) - (SELECT b1 FROM base),3) AS excess_s1,
  ROUND(AVG(date_diff('day', pick_list_date, shipped_ts::DATE))        - (SELECT b2 FROM base),3) AS excess_s2,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date))        - (SELECT b3 FROM base),3) AS excess_s3
FROM d WHERE on_time='N'""")

# -------- B: channel (problem 1) --------
show("B. By order channel  (problem 1: manual email entry)", """
SELECT order_channel,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1_entry_to_pick,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d GROUP BY 1 ORDER BY on_time_pct""")

show("B2. Email orders by format id", """
SELECT email_format_id,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d WHERE order_channel='Email' GROUP BY 1 ORDER BY 1""")

# -------- C: 8am cutoff (problem 2) --------
show("C. Before vs after the 08:00 pick-list cutoff  (problem 2)", """
SELECT CASE WHEN hour(order_received_ts) < 8 THEN 'a_before_8am' ELSE 'b_after_8am' END AS arrival_window,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1_wait_days,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d GROUP BY 1 ORDER BY 1""")

show("C2. By hour of arrival", """
SELECT hour(order_received_ts) AS hr,
  COUNT(*) AS n,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1_wait_days,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct
FROM d GROUP BY 1 ORDER BY 1""")

show("C3. Channel x cutoff window (separating problem 1 from problem 2)", """
SELECT order_channel,
  CASE WHEN hour(order_received_ts) < 8 THEN 'before_8am' ELSE 'after_8am' END AS win,
  COUNT(*) AS n,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1_wait_days,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct
FROM d GROUP BY 1,2 ORDER BY 1,2""")

# -------- D: warehouse (problem 3) --------
show("D. Warehouse allocation  (problem 3)", """
SELECT (nearest_warehouse_id = fulfilment_warehouse_id) AS shipped_from_nearest,
  nearest_warehouse_had_stock,
  COUNT(*) AS n,
  ROUND(AVG(distance_miles),0) AS avg_miles,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date)),3) AS s3_transit_days,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d GROUP BY 1,2 ORDER BY 1,2""")

# -------- E: routing (problem 4) --- THE $680K QUESTION --------
show("E. Route efficiency  (problem 4 - the $680K question)", """
SELECT COUNT(*) AS n_with_actual,
  ROUND(AVG(planned_route_km),2) AS avg_planned_km,
  ROUND(AVG(actual_route_km),2)  AS avg_actual_km,
  ROUND(AVG(actual_route_km - planned_route_km),2) AS avg_excess_km,
  ROUND(100.0*AVG((actual_route_km - planned_route_km)/planned_route_km),2) AS pct_excess
FROM d WHERE actual_route_km IS NOT NULL""")

show("E2. Does route excess predict lateness?", """
SELECT on_time, COUNT(*) AS n,
  ROUND(AVG(actual_route_km - planned_route_km),2) AS avg_excess_km,
  ROUND(100.0*AVG((actual_route_km - planned_route_km)/planned_route_km),2) AS pct_excess,
  ROUND(AVG(stops_on_route),2) AS avg_stops
FROM d WHERE actual_route_km IS NOT NULL GROUP BY 1 ORDER BY 1""")

show("E3. On-time by route-excess bucket", """
SELECT CASE
    WHEN (actual_route_km-planned_route_km)/planned_route_km < 0.02 THEN 'a <2%'
    WHEN (actual_route_km-planned_route_km)/planned_route_km < 0.05 THEN 'b 2-5%'
    WHEN (actual_route_km-planned_route_km)/planned_route_km < 0.10 THEN 'c 5-10%'
    ELSE 'd >10%' END AS excess_bucket,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d WHERE actual_route_km IS NOT NULL GROUP BY 1 ORDER BY 1""")

# -------- carriers (problem 5) --------
show("F. By carrier  (problem 5: API vs no API)", """
SELECT d.carrier_id, ca.has_api, ca.delivery_event_feed, ca.preferred_supplier_agreement,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN d.on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', d.shipped_ts::DATE, d.delivered_date)),3) AS transit_days,
  ROUND(AVG(d.days_late),3) AS avg_days_late,
  ROUND(AVG(d.carrier_rate_usd),2) AS avg_rate
FROM d JOIN ca ON d.carrier_id=ca.carrier_id
GROUP BY 1,2,3,4 ORDER BY on_time_pct""")

# -------- address (the hidden finding) --------
show("G. Address status  (the finding not in the brief)", """
SELECT address_status, COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(days_late),3) AS avg_days_late,
  ROUND(AVG(redelivery_cost_usd),2) AS avg_redelivery_cost,
  SUM(redelivery_cost_usd) AS total_redelivery_cost
FROM d GROUP BY 1 ORDER BY 1""")

show("G2. Address status by channel", """
SELECT order_channel, address_status, COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct
FROM d GROUP BY 1,2 ORDER BY 1,2""")

# -------- commitment type --------
show("H. By commitment type", """
SELECT commitment_type, COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date)),3) AS s3,
  ROUND(AVG(days_late),3) AS avg_days_late
FROM d GROUP BY 1 ORDER BY on_time_pct""")

# -------- trend over time --------
show("I. On-time trend by month", """
SELECT date_trunc('month', order_received_ts)::DATE AS mth, COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(AVG(date_diff('day', order_received_ts::DATE, pick_list_date)),3) AS s1,
  ROUND(AVG(date_diff('day', shipped_ts::DATE, delivered_date)),3) AS s3,
  ROUND(AVG(CASE WHEN nearest_warehouse_had_stock='N' THEN 1.0 ELSE 0 END)*100,1) AS pct_no_stock_nearest
FROM d GROUP BY 1 ORDER BY 1""")
