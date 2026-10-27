import duckdb, json

con = duckdb.connect()
con.sql("""CREATE VIEW d AS
    SELECT * FROM read_csv('data/deliveries.csv', header=true, sample_size=-1)""")
con.sql("""CREATE VIEW cl AS
    SELECT * FROM read_csv('data/clients.csv', header=true, sample_size=-1)""")
con.sql("""CREATE VIEW ca AS
    SELECT * FROM read_csv('data/carriers.csv', header=true, sample_size=-1)""")
con.sql("""CREATE VIEW sp AS
    SELECT * FROM read_csv('data/sla_penalties.csv', header=true, sample_size=-1)""")

def show(t, sql):
    print("\n" + "="*78); print(t); print("="*78); print(con.sql(sql))

# Flag table: each delivery tagged with which risk conditions apply
con.sql("""CREATE VIEW f AS
SELECT *,
  (address_status='incomplete')                               AS bad_address,
  (nearest_warehouse_id <> fulfilment_warehouse_id)           AS far_warehouse,
  (hour(order_received_ts) >= 8)                              AS missed_cutoff,
  (carrier_id IN ('D','E','F'))                               AS no_api_carrier,
  (commitment_type='same_day')                                AS same_day,
  (actual_route_km IS NOT NULL
     AND (actual_route_km-planned_route_km)/planned_route_km > 0.10) AS route_excess_10
FROM d""")

show("1. On-time rate when each condition is PRESENT vs ABSENT", """
SELECT 'bad_address' AS cond,
  SUM(CASE WHEN bad_address THEN 1 ELSE 0 END) AS n_present,
  ROUND(100.0*SUM(CASE WHEN bad_address AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN bad_address THEN 1 ELSE 0 END),0),1) AS ontime_present,
  ROUND(100.0*SUM(CASE WHEN NOT bad_address AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT bad_address THEN 1 ELSE 0 END),0),1) AS ontime_absent
FROM f
UNION ALL SELECT 'far_warehouse',
  SUM(CASE WHEN far_warehouse THEN 1 ELSE 0 END),
  ROUND(100.0*SUM(CASE WHEN far_warehouse AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN far_warehouse THEN 1 ELSE 0 END),0),1),
  ROUND(100.0*SUM(CASE WHEN NOT far_warehouse AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT far_warehouse THEN 1 ELSE 0 END),0),1) FROM f
UNION ALL SELECT 'missed_cutoff',
  SUM(CASE WHEN missed_cutoff THEN 1 ELSE 0 END),
  ROUND(100.0*SUM(CASE WHEN missed_cutoff AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN missed_cutoff THEN 1 ELSE 0 END),0),1),
  ROUND(100.0*SUM(CASE WHEN NOT missed_cutoff AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT missed_cutoff THEN 1 ELSE 0 END),0),1) FROM f
UNION ALL SELECT 'no_api_carrier',
  SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END),
  ROUND(100.0*SUM(CASE WHEN no_api_carrier AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END),0),1),
  ROUND(100.0*SUM(CASE WHEN NOT no_api_carrier AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT no_api_carrier THEN 1 ELSE 0 END),0),1) FROM f
UNION ALL SELECT 'route_excess_10',
  SUM(CASE WHEN route_excess_10 THEN 1 ELSE 0 END),
  ROUND(100.0*SUM(CASE WHEN route_excess_10 AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN route_excess_10 THEN 1 ELSE 0 END),0),1),
  ROUND(100.0*SUM(CASE WHEN NOT route_excess_10 AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT route_excess_10 THEN 1 ELSE 0 END),0),1) FROM f
UNION ALL SELECT 'same_day_promise',
  SUM(CASE WHEN same_day THEN 1 ELSE 0 END),
  ROUND(100.0*SUM(CASE WHEN same_day AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN same_day THEN 1 ELSE 0 END),0),1),
  ROUND(100.0*SUM(CASE WHEN NOT same_day AND on_time='Y' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT same_day THEN 1 ELSE 0 END),0),1) FROM f
""")

show("2. EXCESS late deliveries attributable to each condition", """
WITH tot AS (SELECT COUNT(*) AS n_all, SUM(CASE WHEN on_time='N' THEN 1 ELSE 0 END) AS n_late FROM f)
SELECT cond, n_present, late_present, late_rate_present, late_rate_absent,
  ROUND(n_present*(late_rate_present-late_rate_absent)/100.0,0) AS excess_late,
  ROUND(100.0*n_present*(late_rate_present-late_rate_absent)/100.0/(SELECT n_late FROM tot),1) AS pct_of_all_late
FROM (
  SELECT 'bad_address' AS cond,
    SUM(CASE WHEN bad_address THEN 1 ELSE 0 END) AS n_present,
    SUM(CASE WHEN bad_address AND on_time='N' THEN 1 ELSE 0 END) AS late_present,
    ROUND(100.0*SUM(CASE WHEN bad_address AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN bad_address THEN 1 ELSE 0 END),0),2) AS late_rate_present,
    ROUND(100.0*SUM(CASE WHEN NOT bad_address AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT bad_address THEN 1 ELSE 0 END),0),2) AS late_rate_absent FROM f
  UNION ALL SELECT 'far_warehouse',
    SUM(CASE WHEN far_warehouse THEN 1 ELSE 0 END),
    SUM(CASE WHEN far_warehouse AND on_time='N' THEN 1 ELSE 0 END),
    ROUND(100.0*SUM(CASE WHEN far_warehouse AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN far_warehouse THEN 1 ELSE 0 END),0),2),
    ROUND(100.0*SUM(CASE WHEN NOT far_warehouse AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT far_warehouse THEN 1 ELSE 0 END),0),2) FROM f
  UNION ALL SELECT 'missed_cutoff',
    SUM(CASE WHEN missed_cutoff THEN 1 ELSE 0 END),
    SUM(CASE WHEN missed_cutoff AND on_time='N' THEN 1 ELSE 0 END),
    ROUND(100.0*SUM(CASE WHEN missed_cutoff AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN missed_cutoff THEN 1 ELSE 0 END),0),2),
    ROUND(100.0*SUM(CASE WHEN NOT missed_cutoff AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT missed_cutoff THEN 1 ELSE 0 END),0),2) FROM f
  UNION ALL SELECT 'no_api_carrier',
    SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END),
    SUM(CASE WHEN no_api_carrier AND on_time='N' THEN 1 ELSE 0 END),
    ROUND(100.0*SUM(CASE WHEN no_api_carrier AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END),0),2),
    ROUND(100.0*SUM(CASE WHEN NOT no_api_carrier AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT no_api_carrier THEN 1 ELSE 0 END),0),2) FROM f
  UNION ALL SELECT 'route_excess_10',
    SUM(CASE WHEN route_excess_10 THEN 1 ELSE 0 END),
    SUM(CASE WHEN route_excess_10 AND on_time='N' THEN 1 ELSE 0 END),
    ROUND(100.0*SUM(CASE WHEN route_excess_10 AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN route_excess_10 THEN 1 ELSE 0 END),0),2),
    ROUND(100.0*SUM(CASE WHEN NOT route_excess_10 AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT route_excess_10 THEN 1 ELSE 0 END),0),2) FROM f
  UNION ALL SELECT 'same_day_promise',
    SUM(CASE WHEN same_day THEN 1 ELSE 0 END),
    SUM(CASE WHEN same_day AND on_time='N' THEN 1 ELSE 0 END),
    ROUND(100.0*SUM(CASE WHEN same_day AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN same_day THEN 1 ELSE 0 END),0),2),
    ROUND(100.0*SUM(CASE WHEN NOT same_day AND on_time='N' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN NOT same_day THEN 1 ELSE 0 END),0),2) FROM f
) ORDER BY excess_late DESC""")

show("3. How many late orders have NO risk flag at all (baseline noise)", """
SELECT bad_address, far_warehouse, missed_cutoff, no_api_carrier,
  COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct
FROM f GROUP BY 1,2,3,4 HAVING COUNT(*)>300 ORDER BY on_time_pct""")

show("4. Clean orders: no bad address, nearest warehouse, API carrier, before cutoff", """
SELECT COUNT(*) AS n,
  ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct
FROM f WHERE NOT bad_address AND NOT far_warehouse AND NOT no_api_carrier AND NOT missed_cutoff""")

show("5. SIMULATION: on-time rate if each single condition were eliminated", """
WITH base AS (SELECT ROUND(100.0*SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),2) AS b FROM f)
SELECT 'current' AS scenario, (SELECT b FROM base) AS on_time_pct, 0.0 AS gain
UNION ALL
SELECT 'fix_addresses',
  ROUND(100.0*(SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END)
     + SUM(CASE WHEN bad_address AND on_time='N' THEN 1 ELSE 0 END)
       * (SELECT 1.0*SUM(CASE WHEN NOT bad_address AND on_time='Y' THEN 1 ELSE 0 END)/SUM(CASE WHEN NOT bad_address THEN 1 ELSE 0 END) FROM f)
     - SUM(CASE WHEN bad_address AND on_time='Y' THEN 1 ELSE 0 END)
       * (1-(SELECT 1.0*SUM(CASE WHEN NOT bad_address AND on_time='Y' THEN 1 ELSE 0 END)/SUM(CASE WHEN NOT bad_address THEN 1 ELSE 0 END) FROM f))
    )/COUNT(*),2), 0.0 FROM f
""")

# Cleaner simulation in python below
res = {}
rows = con.sql("""SELECT
  COUNT(*) AS n_all,
  SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END) AS n_ot,
  SUM(CASE WHEN bad_address THEN 1 ELSE 0 END) AS n_bad,
  SUM(CASE WHEN bad_address AND on_time='Y' THEN 1 ELSE 0 END) AS ot_bad,
  SUM(CASE WHEN NOT bad_address THEN 1 ELSE 0 END) AS n_ok,
  SUM(CASE WHEN NOT bad_address AND on_time='Y' THEN 1 ELSE 0 END) AS ot_ok,
  SUM(CASE WHEN far_warehouse THEN 1 ELSE 0 END) AS n_far,
  SUM(CASE WHEN far_warehouse AND on_time='Y' THEN 1 ELSE 0 END) AS ot_far,
  SUM(CASE WHEN NOT far_warehouse THEN 1 ELSE 0 END) AS n_near,
  SUM(CASE WHEN NOT far_warehouse AND on_time='Y' THEN 1 ELSE 0 END) AS ot_near,
  SUM(CASE WHEN missed_cutoff THEN 1 ELSE 0 END) AS n_miss,
  SUM(CASE WHEN missed_cutoff AND on_time='Y' THEN 1 ELSE 0 END) AS ot_miss,
  SUM(CASE WHEN NOT missed_cutoff THEN 1 ELSE 0 END) AS n_hit,
  SUM(CASE WHEN NOT missed_cutoff AND on_time='Y' THEN 1 ELSE 0 END) AS ot_hit,
  SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END) AS n_noapi,
  SUM(CASE WHEN no_api_carrier AND on_time='Y' THEN 1 ELSE 0 END) AS ot_noapi,
  SUM(CASE WHEN NOT no_api_carrier THEN 1 ELSE 0 END) AS n_api,
  SUM(CASE WHEN NOT no_api_carrier AND on_time='Y' THEN 1 ELSE 0 END) AS ot_api,
  SUM(CASE WHEN route_excess_10 THEN 1 ELSE 0 END) AS n_rx,
  SUM(CASE WHEN route_excess_10 AND on_time='Y' THEN 1 ELSE 0 END) AS ot_rx,
  SUM(CASE WHEN NOT route_excess_10 THEN 1 ELSE 0 END) AS n_nrx,
  SUM(CASE WHEN NOT route_excess_10 AND on_time='Y' THEN 1 ELSE 0 END) AS ot_nrx
FROM f""").fetchall()[0]
c = dict(zip([x[0] for x in con.sql("SELECT 1").description] if False else
  ['n_all','n_ot','n_bad','ot_bad','n_ok','ot_ok','n_far','ot_far','n_near','ot_near',
   'n_miss','ot_miss','n_hit','ot_hit','n_noapi','ot_noapi','n_api','ot_api',
   'n_rx','ot_rx','n_nrx','ot_nrx'], rows))

base = 100.0*c['n_ot']/c['n_all']
def lift(n_bad, ot_bad, n_ok, ot_ok):
    good_rate = ot_ok/n_ok
    new_ot = c['n_ot'] - ot_bad + n_bad*good_rate
    return 100.0*new_ot/c['n_all']

print("\n" + "="*78)
print("6. SINGLE-FIX SIMULATION (on-time % if that condition were removed)")
print("="*78)
print(f"  current on-time                         : {base:.1f}%")
sims = {
 'fix addresses at ingestion'      : lift(c['n_bad'],  c['ot_bad'],  c['n_ok'],   c['ot_ok']),
 'always ship from nearest warehouse': lift(c['n_far'],  c['ot_far'],  c['n_near'], c['ot_near']),
 'remove the 8am cutoff'           : lift(c['n_miss'], c['ot_miss'], c['n_hit'],  c['ot_hit']),
 'move volume to API carriers'     : lift(c['n_noapi'],c['ot_noapi'],c['n_api'],  c['ot_api']),
 'routing software (fix >10% route excess)': lift(c['n_rx'], c['ot_rx'], c['n_nrx'], c['ot_nrx']),
}
for k,v in sorted(sims.items(), key=lambda x:-x[1]):
    print(f"  {k:40s}: {v:.1f}%   (+{v-base:.1f} pts)")

res = {'current_on_time': round(base,1), 'scenarios': {k: round(v,1) for k,v in sims.items()}}
with open('results_raw.json','w') as fh: json.dump(res, fh, indent=2)

show("7. Business cost: SLA penalties + redelivery by condition", """
SELECT
  SUM(CASE WHEN address_status='incomplete' THEN redelivery_cost_usd ELSE 0 END) AS redeliv_bad_addr,
  SUM(redelivery_cost_usd) AS redeliv_total,
  (SELECT SUM(penalty_usd) FROM sp) AS sla_penalties_total,
  (SELECT COUNT(*) FROM sp) AS n_penalties
FROM d""")

show("8. The 9 at-risk accounts: what is actually hurting them", """
SELECT cl.account_status, COUNT(*) AS deliveries,
  COUNT(DISTINCT d.client_id) AS clients,
  ROUND(100.0*SUM(CASE WHEN d.on_time='Y' THEN 1 ELSE 0 END)/COUNT(*),1) AS on_time_pct,
  ROUND(100.0*AVG(CASE WHEN d.address_status='incomplete' THEN 1.0 ELSE 0 END),1) AS pct_bad_addr,
  ROUND(100.0*AVG(CASE WHEN d.nearest_warehouse_id<>d.fulfilment_warehouse_id THEN 1.0 ELSE 0 END),1) AS pct_far_wh,
  ROUND(100.0*AVG(CASE WHEN d.carrier_id IN ('D','E','F') THEN 1.0 ELSE 0 END),1) AS pct_no_api_carrier
FROM d JOIN cl ON d.client_id=cl.client_id
GROUP BY 1 ORDER BY 1""")

show("9. Revenue of threatening accounts", """
SELECT COUNT(*) AS n_clients, SUM(annual_revenue_usd) AS revenue_at_risk
FROM cl WHERE account_status='threatening_to_leave'""")

show("10. Complaints mapped to their delivery's conditions", """
SELECT cm.category, COUNT(*) AS n,
  ROUND(100.0*AVG(CASE WHEN d.address_status='incomplete' THEN 1.0 ELSE 0 END),0) AS pct_bad_addr,
  ROUND(100.0*AVG(CASE WHEN d.nearest_warehouse_id<>d.fulfilment_warehouse_id THEN 1.0 ELSE 0 END),0) AS pct_far_wh,
  ROUND(100.0*AVG(CASE WHEN d.carrier_id IN ('D','E','F') THEN 1.0 ELSE 0 END),0) AS pct_no_api
FROM read_csv('data/complaints.csv', header=true) cm
JOIN d ON cm.delivery_id=d.delivery_id
GROUP BY 1 ORDER BY n DESC""")

show("11. Customer success time study", """
SELECT ROUND(SUM(hours_on_delivery_complaints),1) AS hrs_complaints,
       ROUND(SUM(hours_keying_email_orders),1) AS hrs_keying,
       ROUND(SUM(hours_on_proactive_client_work),1) AS hrs_proactive,
       ROUND(SUM(hours_total),1) AS hrs_total,
       ROUND(100.0*SUM(hours_on_delivery_complaints)/SUM(hours_total),1) AS pct_on_complaints,
       ROUND(100.0*SUM(hours_keying_email_orders)/SUM(hours_total),1) AS pct_on_keying
FROM read_csv('data/customer_success_time_study.csv', header=true)""")
