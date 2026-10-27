"""Carrier selection agent, operating on the real delivery records.

Fix 2 from the analysis, and the largest single fix at +5.5 points. Floor
staff choose a carrier from a printed rate card and a preference
spreadsheet, so 21,183 shipments went to carriers D, E and F -- the three
with no API integration, running 65.5% on-time against 75.3% for A, B and C.

It invents nothing. `carrier_id` in deliveries.csv and `has_api` in
carriers.csv already state which carrier took each shipment and whether it
reports events. The agent screens on those, quantifies what reassignment
recovers, and prices the rate increase against the penalties it avoids.

Every number traces to a row in deliveries.csv or carriers.csv.
"""
import itertools

FLAGS = ["bad_address", "far_warehouse", "missed_cutoff", "no_api_carrier"]
OTHER_FLAGS = [f for f in FLAGS if f != "no_api_carrier"]
#you are defining other flags because quantify uses those remaining flags to compare no_api carriers
#with api carriers that have share the same delivery orders
MIN_DONOR_ROWS = 30 #minimum for trustworthiness


# ------------------------------------------------------------------- screen
def screen(con) -> dict:
    """Which shipments went to a carrier with no event feed, and at what rate."""
    totals = con.sql("""
        -- Return one summary row for the full delivery dataset.
        SELECT COUNT(*),
               -- Count shipments assigned to carriers without an API.
               SUM(CASE WHEN no_api_carrier THEN 1 ELSE 0 END),
               -- Of those shipments, count the ones that were not on time.
               SUM(CASE WHEN no_api_carrier AND NOT ot THEN 1 ELSE 0 END),
               -- Average freight rate for non-API shipments. CASE returns NULL
               -- for other shipments, and AVG ignores those NULL values.
               AVG(CASE WHEN no_api_carrier THEN carrier_rate_usd END),
               -- Average freight rate for shipments assigned to API carriers.
               AVG(CASE WHEN NOT no_api_carrier THEN carrier_rate_usd END)
        -- f is the delivery view, with one row per shipment and derived flags.
        FROM f""").fetchall()[0]  # Execute the query and take its single result row.

    by_carrier = con.sql("""
        -- Build one summary row for each carrier and its carrier-table details.
        SELECT f.carrier_id,
               -- These three values come from the carrier reference view ca.
               ca.has_api,
               ca.preferred_supplier_agreement,
               ca.delivery_event_feed,
               -- Count delivery records for this carrier.
               COUNT(*) AS orders,
               -- Mean rate per shipment, rounded to cents.
               ROUND(AVG(f.carrier_rate_usd), 2) AS avg_rate,
               -- Convert the share of on-time records into a percentage.
               ROUND(100.0 * SUM(CASE WHEN f.ot THEN 1 ELSE 0 END)
                     / COUNT(*), 1) AS on_time_pct,
               -- Difference in calendar days between shipment date and delivery.
               ROUND(AVG(date_diff('day', f.shipped_ts::DATE,
                                   f.delivered_date)), 2) AS transit_days
        -- f is the delivery/flag view; ca supplies carrier attributes.
        FROM f JOIN ca ON f.carrier_id = ca.carrier_id
        -- JOIN keeps only deliveries whose carrier_id matches a row in ca.
        -- Group by selected-column positions 1-4: carrier_id and its attributes.
        GROUP BY 1, 2, 3, 4
        -- Sort from the lowest on-time percentage to the highest.
        ORDER BY on_time_pct""").fetchall()

    # Best and worst carrier on each lane -- what a live selector would use.
    by_lane = con.sql("""
        -- First calculate per-region/per-carrier performance.
        WITH lane AS (
            -- A lane here means one destination region served by one carrier.
            SELECT destination_region AS region,
                   carrier_id,
                   COUNT(*) AS orders,
                   -- On-time shipments divided by all shipments in this lane.
                   100.0 * SUM(CASE WHEN ot THEN 1 ELSE 0 END) / COUNT(*) AS on_time
            FROM f
            -- Group by destination region and carrier to compute their lane stats.
            GROUP BY 1, 2
            -- Ignore region/carrier combinations with fewer than 100 shipments.
            HAVING COUNT(*) >= 100
        )
        -- From the eligible carrier rows, pick the best and worst carrier
        -- and their on-time percentages for each region.
        SELECT region,
               -- arg_max returns the carrier_id from the row with the highest
               -- on_time value; MAX returns that highest value itself.
               arg_max(carrier_id, on_time) AS best_carrier,
               ROUND(MAX(on_time), 1)       AS best_on_time,
               -- arg_min returns the carrier_id from the row with the lowest
               -- on_time value; MIN returns that lowest value itself.
               arg_min(carrier_id, on_time) AS worst_carrier,
               ROUND(MIN(on_time), 1)       AS worst_on_time
        FROM lane
        GROUP BY 1
        ORDER BY 1""").fetchall()
    # fetchall() turns each query result into rows of values for the Python code.

    n_all, n_no_api = int(totals[0]), int(totals[1])
    return {
        "orders_screened": n_all,
        "on_non_api_carrier": n_no_api,
        "non_api_that_went_late": int(totals[2]),
        "pct_of_book_non_api": round(100 * n_no_api / n_all, 1),
        "avg_rate_non_api_usd": round(float(totals[3]), 2),
        "avg_rate_api_usd": round(float(totals[4]), 2),
        "by_carrier": [
            {"carrier_id": r[0], "has_api": r[1], "preferred_supplier": r[2],
             "event_feed": r[3], "orders": int(r[4]), "avg_rate_usd": float(r[5]),
             "on_time_pct": float(r[6]), "transit_days": float(r[7])}
            for r in by_carrier
        ],
        "by_lane": [
            {"region": r[0], "best_carrier": r[1], "best_on_time_pct": float(r[2]),
             "worst_carrier": r[3], "worst_on_time_pct": float(r[4]),
             "spread_pts": round(float(r[2]) - float(r[4]), 1)}
            for r in by_lane
        ],
    }


# ----------------------------------------------------------------- quantify
def quantify(con) -> dict:
    """What reassigning those shipments to API carriers recovers.

    Each non-API cohort is projected at the rate of the cohort that shares
    its other problems but used an API carrier. No cohort is given the
    fully clean rate unless it is otherwise clean.
    """
    # Select each risk flag plus the on-time boolean from the delivery view.
    # The f-string joins FLAGS into SQL column names; .df() returns the result
    # as a pandas DataFrame for the cohort calculations below.
    df = con.sql(f"SELECT {', '.join(FLAGS)}, ot FROM f").df()
    n_all = len(df) #total 48340 rows
    baseline = 100.0 * df.ot.sum() / n_all #of all 48340 them , the ones on time are 34321

    rows, projected_on_time = [], 0.0
    for combo in itertools.product([False, True], repeat=len(OTHER_FLAGS)):
        sel = df.index == df.index
        for col, val in zip(OTHER_FLAGS, combo):
            sel = sel & (df[col] == val)
        #let's take an example
        #let's say combination is (False, False, False). none apply
        #so the for col,val in zip(other flags, combo) basically assigns 
        #bad_addr=False, Far_wh=false, missed_cutoff=False
        broken = sel & df.no_api_carrier #selects the matching shipments on non-api carriers
        donor = sel & ~df.no_api_carrier#selecting matching shipments on api carriers
        n_broken, n_donor = int(broken.sum()), int(donor.sum())
        #count those selected rows

        projected_on_time += df.loc[donor, "ot"].sum()
        #among 6554 api shipments, 6212 were on time.
        if n_broken == 0:
            continue
        if n_donor >= MIN_DONOR_ROWS: #n_donor=6554>30, so rate=6212/6554=94.8%
            rate = df.loc[donor, "ot"].mean()
        else:
            rate = df.loc[broken, "ot"].mean()# assume no improvement

        now = df.loc[broken, "ot"].mean() #of 7384 non api shipments, 5090 were on time.
        #now is 68.9%
        projected_on_time += n_broken * rate
        #projected_on_time=7384*94.8% - 6999
        rows.append({
            "remaining_problems": ", ".join(
                c.replace("_", " ") for c, v in zip(OTHER_FLAGS, combo) if v
            ) or "none",
            "orders_reassigned": n_broken,
            "their_on_time_pct": round(100 * now, 1),
            "donor_on_time_pct": round(100 * rate, 1),
            "donor_rows": n_donor,
            "recovered": round(n_broken * (rate - now)),
        })

    projected = 100.0 * projected_on_time / n_all
    rows.sort(key=lambda r: -r["recovered"]) #sorts summaries by recovered largest first
    #recovery is the estimated additional on time deliveries
    return { 
        "current_on_time_pct": round(baseline, 1), #71%
        "projected_on_time_pct": round(projected, 1), #76.5%
        "gain_pts": round(projected - baseline, 1), #5.5 percentage points
        "deliveries_recovered": sum(r["recovered"] for r in rows), #2671 deliveries
        "cohorts": rows,
    }


# --------------------------------------------------------------------- cost
def cost(con, screened: dict) -> dict:
    """The rate increase from reassignment, against the failure cost today.

    Reassignment is not free: API carriers charge more. This is the trade
    leadership actually has to make.
    """
    row = con.sql("""
        -- The outer query totals redelivery costs across every delivery row.
        SELECT SUM(redelivery_cost_usd),
               -- This scalar subquery separately totals all SLA penalties.
               (SELECT SUM(penalty_usd) FROM sp),
               -- Count penalty records; this is returned as the third value.
               (SELECT COUNT(*) FROM sp)
        FROM f""").fetchall()[0]  # One row containing the three requested totals.

    n = screened["on_non_api_carrier"]
    uplift_per_shipment = screened["avg_rate_api_usd"] - screened["avg_rate_non_api_usd"]
    extra_freight_6mo = n * uplift_per_shipment

    redelivery_6mo, penalties_6mo = int(row[0]), int(row[1])
    failure_6mo = redelivery_6mo + penalties_6mo

    return {
        "shipments_reassigned": n,
        "rate_uplift_per_shipment_usd": round(uplift_per_shipment, 2),
        "extra_freight_6mo_usd": round(extra_freight_6mo),
        "extra_freight_annualised_usd": round(extra_freight_6mo * 2),
        "sla_penalties_6mo_usd": penalties_6mo,
        "sla_penalty_count": int(row[2]),
        "redelivery_6mo_usd": redelivery_6mo,
        "failure_cost_6mo_usd": failure_6mo,
        "failure_cost_annualised_usd": failure_6mo * 2,
        "failure_cost_times_freight_uplift": round(failure_6mo / extra_freight_6mo, 1)
        if extra_freight_6mo else None,
    }


# ------------------------------------------------------------------ decision
def decide(screened: dict, quantified: dict, costed: dict,
           vendor_price_usd: int = 680_000,
           vendor_gain_pts: float = 2.0) -> dict:
    """What to do, priced against the routing vendor."""
    gain = quantified["gain_pts"]



#This adds the on-time percentages of the three API carriers and 
# divides by the number of API carriers:
    worst = min(screened["by_carrier"], key=lambda c: c["on_time_pct"])
    preferred = [c for c in screened["by_carrier"]
                 if c["preferred_supplier"] == "Y"]
    api_avg = sum(c["on_time_pct"] for c in screened["by_carrier"]
                  if c["has_api"] == "Y") / max(
                      1, sum(1 for c in screened["by_carrier"] if c["has_api"] == "Y"))

    actions = [
        "select carriers on recent lane performance, not a printed rate card",
        f"exit or cap carrier {worst['carrier_id']} "
        f"({worst['on_time_pct']}% on-time at ${worst['avg_rate_usd']})",
    ]
    for p in preferred:
        actions.append(
            f"revisit the preferred-supplier agreement with carrier "
            f"{p['carrier_id']}: {p['on_time_pct']}% on-time, "
            f"{round(api_avg - p['on_time_pct'], 1)} points below the API average"
        )

    return {
        "actions": actions,
        "orders_affected": screened["on_non_api_carrier"],
        "gain_pts": gain,
        "vendor_gain_pts": vendor_gain_pts,
        "times_better_than_vendor": round(gain / vendor_gain_pts, 1)
        if vendor_gain_pts else None,
        "vendor_cost_per_point_usd": round(vendor_price_usd / vendor_gain_pts)
        if vendor_gain_pts else None,
        "this_fix_cost_per_point_usd": round(
            costed["extra_freight_annualised_usd"] / gain) if gain else None,
        "api_carrier_avg_on_time_pct": round(api_avg, 1),
    }


NARRATIVE_PROMPT = """You are writing three sentences for an operations report.

Figures, all measured from 48,340 delivery records:
- shipments given to a carrier with no event feed: {n_no_api}
- of those, how many were delivered late: {late}
- on-time rate of carriers with no event feed: {non_api_on_time}%
- on-time rate of carriers with an event feed: {api_on_time}%
- on-time rate today: {current}%
- projected on-time rate if those shipments moved to API carriers: {projected}%
- extra freight cost per year of moving them: ${freight}
- service-level penalties and redelivery paid in six months: ${failure}

Write three plain sentences stating what the figures show, what the trade-off
is, and what to do. Use only the figures given. Do not add numbers, causes or
recommendations that are not in the list above.
"""


def narrate(screened: dict, quantified: dict, costed: dict, llm=None) -> str:
    """Plain-language summary. The model writes words, never numbers."""
    non_api = [c for c in screened["by_carrier"] if c["has_api"] == "N"]
    api = [c for c in screened["by_carrier"] if c["has_api"] == "Y"]
    non_api_ot = round(
        sum(c["on_time_pct"] * c["orders"] for c in non_api)
        / sum(c["orders"] for c in non_api), 1)
    api_ot = round(
        sum(c["on_time_pct"] * c["orders"] for c in api)
        / sum(c["orders"] for c in api), 1)

    fallback = (
        f"{screened['on_non_api_carrier']:,} of "
        f"{screened['orders_screened']:,} shipments went to a carrier with no "
        f"event feed, and {screened['non_api_that_went_late']:,} of those were "
        f"delivered late. Those carriers run {non_api_ot}% on-time against "
        f"{api_ot}% for the three with an API, at an average "
        f"${screened['avg_rate_non_api_usd']} per shipment versus "
        f"${screened['avg_rate_api_usd']}. Reassigning them projects on-time "
        f"delivery from {quantified['current_on_time_pct']}% to "
        f"{quantified['projected_on_time_pct']}% for "
        f"${costed['extra_freight_annualised_usd']:,} a year in extra freight, "
        f"against ${costed['failure_cost_6mo_usd']:,} already paid in penalties "
        f"and redelivery over six months."
    )
    if llm is None:
        return fallback
    try:
        return llm.invoke(NARRATIVE_PROMPT.format(
            n_no_api=screened["on_non_api_carrier"],
            late=screened["non_api_that_went_late"],
            non_api_on_time=non_api_ot,
            api_on_time=api_ot,
            current=quantified["current_on_time_pct"],
            projected=quantified["projected_on_time_pct"],
            freight=costed["extra_freight_annualised_usd"],
            failure=costed["failure_cost_6mo_usd"],
        )).content.strip()
    except Exception:
        return fallback
