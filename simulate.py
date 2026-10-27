import duckdb, json, itertools

con = duckdb.connect()
con.sql("""CREATE VIEW d AS
    SELECT * FROM read_csv('data/deliveries.csv', header=true, sample_size=-1)""")
con.sql("""CREATE VIEW f AS
SELECT *,
  (address_status='incomplete')                     AS bad_address,
  (nearest_warehouse_id <> fulfilment_warehouse_id) AS far_warehouse,
  (hour(order_received_ts) >= 8)                    AS missed_cutoff,
  (carrier_id IN ('D','E','F'))                     AS no_api_carrier,
  (on_time='Y')                                     AS ot
FROM d""")

df = con.sql("SELECT bad_address, far_warehouse, missed_cutoff, no_api_carrier, ot FROM f").df()
N = len(df)
base = 100.0*df.ot.sum()/N

CONDS = ['bad_address','far_warehouse','missed_cutoff','no_api_carrier']
LABEL = {
 'bad_address'   : 'Validate addresses at ingestion',
 'far_warehouse' : 'Ship from nearest stocked warehouse',
 'missed_cutoff' : 'Multiple pick waves (remove 08:00 cutoff)',
 'no_api_carrier': 'Shift volume to API carriers',
}

def sim(fixed):
    """on-time % if every condition in `fixed` were eliminated.
    Each affected order is re-assigned the empirical on-time rate of the
    matching cohort that already has none of the fixed conditions present."""
    mask_clean = ~df[list(fixed)].any(axis=1) if fixed else df.index == df.index
    # cohort rate conditioned on the remaining (unfixed) conditions
    rest = [c for c in CONDS if c not in fixed]
    total_ot = 0.0
    for combo in itertools.product([False, True], repeat=len(rest)):
        sel = df.index == df.index
        for c, v in zip(rest, combo):
            sel = sel & (df[c] == v)
        n_sel = sel.sum()
        if n_sel == 0:
            continue
        donor = sel & mask_clean
        rate = df.loc[donor, 'ot'].mean() if donor.sum() >= 30 else df.loc[sel, 'ot'].mean()
        total_ot += n_sel * rate
    return 100.0*total_ot/N

print(f"current on-time: {base:.1f}%\n")
print("SINGLE FIXES")
singles = {}
for c in CONDS:
    v = sim({c}); singles[c] = v
    print(f"  {LABEL[c]:45s} {v:5.1f}%  (+{v-base:4.1f})")

print("\nROUTING VENDOR (its own claim, best case)")
# Routing software addresses carrier/route selection only.
rx = con.sql("""
SELECT
  SUM(CASE WHEN actual_route_km IS NOT NULL AND (actual_route_km-planned_route_km)/planned_route_km>0.10 THEN 1 ELSE 0 END) AS n_rx,
  SUM(CASE WHEN actual_route_km IS NOT NULL AND (actual_route_km-planned_route_km)/planned_route_km>0.10 AND on_time='Y' THEN 1 ELSE 0 END) AS ot_rx,
  SUM(CASE WHEN NOT(actual_route_km IS NOT NULL AND (actual_route_km-planned_route_km)/planned_route_km>0.10) THEN 1 ELSE 0 END) AS n_ok,
  SUM(CASE WHEN NOT(actual_route_km IS NOT NULL AND (actual_route_km-planned_route_km)/planned_route_km>0.10) AND on_time='Y' THEN 1 ELSE 0 END) AS ot_ok,
  COUNT(*) AS n_all, SUM(CASE WHEN on_time='Y' THEN 1 ELSE 0 END) AS n_ot
FROM d""").fetchall()[0]
n_rx, ot_rx, n_ok, ot_ok, n_all, n_ot = rx
routing = 100.0*(n_ot - ot_rx + n_rx*(ot_ok/n_ok))/n_all
print(f"  Optimise every >10%-excess route              {routing:5.1f}%  (+{routing-base:4.1f})")

print("\nCOMBINED FIXES")
combos = [
  ('bad_address',),
  ('bad_address','far_warehouse'),
  ('bad_address','far_warehouse','no_api_carrier'),
  ('bad_address','far_warehouse','no_api_carrier','missed_cutoff'),
]
combo_res = {}
for cb in combos:
    v = sim(set(cb))
    name = " + ".join(LABEL[c] for c in cb)
    combo_res[name] = round(v,1)
    print(f"  {len(cb)} fix(es): {v:5.1f}%  (+{v-base:4.1f})   [{', '.join(cb)}]")

print(f"\nBENCHMARK: 94.0%")
print(f"Orders already clean on all four: "
      f"{(~df[CONDS].any(axis=1)).sum()} of {N} "
      f"-> {100.0*df.loc[~df[CONDS].any(axis=1),'ot'].mean():.1f}% on-time")

# ---- attribution shares of excess lateness ----
excess = {}
for c in CONDS:
    pres = df[df[c]]; abst = df[~df[c]]
    lr_p = 1-pres.ot.mean(); lr_a = 1-abst.ot.mean()
    excess[c] = len(pres)*(lr_p-lr_a)
excess['route_selection'] = n_rx*((1-ot_rx/n_rx)-(1-ot_ok/n_ok))
tot_late = N - df.ot.sum()
unexplained = tot_late - sum(excess.values())

print("\nSHARE OF ALL LATE DELIVERIES")
for k,v in sorted(excess.items(), key=lambda x:-x[1]):
    print(f"  {k:20s} {v:7.0f} late  = {100*v/tot_late:4.1f}% of all late")
print(f"  {'baseline/unexplained':20s} {unexplained:7.0f} late  = {100*unexplained/tot_late:4.1f}%")

out = {
  "generated": "halcyon root-cause analysis",
  "n_deliveries": int(N),
  "current_on_time_pct": round(base,1),
  "industry_benchmark_pct": 94.0,
  "clean_order_on_time_pct": round(100.0*df.loc[~df[CONDS].any(axis=1),'ot'].mean(),1),
  "late_delivery_attribution_pct": {
      "bad_address_at_ingestion": round(100*excess['bad_address']/tot_late,1),
      "non_api_carrier_choice":   round(100*excess['no_api_carrier']/tot_late,1),
      "warehouse_allocation":     round(100*excess['far_warehouse']/tot_late,1),
      "route_selection":          round(100*excess['route_selection']/tot_late,1),
      "pick_list_cutoff":         round(100*excess['missed_cutoff']/tot_late,1),
      "baseline_unexplained":     round(100*unexplained/tot_late,1),
  },
  "single_fix_on_time_pct": {LABEL[c]: round(v,1) for c,v in singles.items()},
  "routing_vendor_on_time_pct": round(routing,1),
  "combined_fix_on_time_pct": combo_res,
}
with open('results.json','w') as fh: json.dump(out, fh, indent=2)
print("\nwrote results.json")
