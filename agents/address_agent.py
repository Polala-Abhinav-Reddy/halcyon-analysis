"""Address intake agent, operating on the real delivery records.

Fix 1 from the analysis. Nothing at Halcyon stops an order with an
incomplete address, so 5,994 of them shipped and ran at 35.4% on-time
against 76.0% for complete ones. This agent is the missing hold.

It parses nothing and invents nothing. `address_status` in deliveries.csv
already states whether an address was complete at ingestion; the agent
screens on that column, decides hold or release, and quantifies what the
hold recovers using observed cohort rates.

Every number it reports traces to a row in deliveries.csv.
"""
import itertools
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import response_text

FLAGS = ["bad_address", "far_warehouse", "missed_cutoff", "no_api_carrier"]
OTHER_FLAGS = [f for f in FLAGS if f != "bad_address"]
#you are defining other flags because quantify uses those remaining flags to 
# compare incomplete address orders with 
#complete address orders that share the same delivery orders
MIN_DONOR_ROWS = 30 #we are traversing through atleast 30 rows,if not it's not trustworthy


# ------------------------------------------------------------------- screen
def screen(con) -> dict:
    """Which orders this agent would hold, and who they belong to."""
    totals = con.sql("""
        SELECT COUNT(*),
               SUM(CASE WHEN bad_address THEN 1 ELSE 0 END),
               SUM(CASE WHEN bad_address AND NOT ot THEN 1 ELSE 0 END)
        FROM f""").fetchall()[0]
#here, you are running sql query for total number of bad addresses and also 
#the bad addresses which went late

    by_channel = con.sql("""    
        SELECT order_channel,
               COUNT(*) AS orders,
               SUM(CASE WHEN bad_address THEN 1 ELSE 0 END) AS would_hold,
               ROUND(100.0 * SUM(CASE WHEN bad_address THEN 1 ELSE 0 END)
                     / COUNT(*), 1) AS pct_held
        FROM f GROUP BY 1 ORDER BY pct_held DESC""").fetchall()  
#you are basically breaking the order channel into email, api and edi

    worst_clients = con.sql("""
        SELECT f.client_id,
               cl.account_status,
               COUNT(*) AS orders,
               SUM(CASE WHEN f.bad_address THEN 1 ELSE 0 END) AS would_hold,
               ROUND(100.0 * SUM(CASE WHEN f.bad_address THEN 1 ELSE 0 END)
                     / COUNT(*), 1) AS pct_held
        FROM f JOIN cl ON f.client_id = cl.client_id
        GROUP BY 1, 2
        HAVING COUNT(*) >= 50
        ORDER BY pct_held DESC
        LIMIT 10""").fetchall()
#here, the sql query is returning the top 10 clients with 
#highest rates, considering the clients with atleastt 50 orders

    return {
        "orders_screened": int(totals[0]), #all addresses counted
        "would_hold": int(totals[1]), #total number of incomplete addresses
        "held_that_went_late": int(totals[2]), #incomplete address orders that were late
        "pct_of_book_held": round(100 * int(totals[1]) / int(totals[0]), 1), 
        #agents flags for a hold means the order has an incomplete address flag.
        # agent holds before dispatching the order
        "by_channel": [
            {"channel": r[0], "orders": int(r[1]),
             "would_hold": int(r[2]), "pct_held": float(r[3])}
            #would hold -> total incomplete addresses
            #pct_held -> total number of addresses holded by the agent because of incompleteness
            for r in by_channel
        ],
        "worst_clients": [
            {"client_id": r[0], "account_status": r[1], "orders": int(r[2]),
             "would_hold": int(r[3]), "pct_held": float(r[4])}
            for r in worst_clients
        ],
    }


# ----------------------------------------------------------------- quantify
def quantify(con) -> dict:
    """What holding those orders recovers, by observed cohort rates.

    Each incomplete-address cohort is projected at the rate of the cohort
    that shares its other problems but has a complete address. No cohort is
    given the fully clean rate unless it is otherwise clean.
    """
    df = con.sql(f"SELECT {', '.join(FLAGS)}, ot FROM f").df() 
#loads all the flags along with the on-time indicator from table f, and 
# then laods them into the pandas dataframe
    n_all = len(df) #counts all delivery records
    baseline = 100.0 * df.ot.sum() / n_all #on-time deliveries percentage

    rows, projected_on_time = [], 0.0
    #rows will hold details for each group of incomplete address orders
    #projected_on_time accumulates the estimated number 
    # of on-time deliveries across entire dataset
    for combo in itertools.product([False, True], repeat=len(OTHER_FLAGS)):
        #other flags has three problems: far wh, missed cutoff, no api carrier
        #for each problem: there can be true or false.
        #itertools.product lists every possible combination of those yes/no values.
        #let's consider combo is (true,false,true)
        #pairs each flag with it's value.
        #now, far_wh=true, missed_cutoff=false and no_api_carrier=true
        sel = df.index == df.index
        for col, val in zip(OTHER_FLAGS, combo):
            sel = sel & (df[col] == val)

        broken = sel & df.bad_address #selects orders in the group with incomplete addresses
        donor = sel & ~df.bad_address #selects orders in group with complete addresses
        n_broken, n_donor = int(broken.sum()), int(donor.sum())
        #counts how many orders are part of each group.

        # Donors keep their own rate; they are not being changed.
        projected_on_time += df.loc[donor, "ot"].sum()
#we add those number of donor orders which were on time again to the projected_on_time list
#imagine there are 10 broken orders and 40 donor orders
#the donor orders stay as they are 
#actual on time deliveries among those donor orders(let's say 28) 
# are added to the projected_on_time
        if n_broken == 0:
            continue
        if n_donor >= MIN_DONOR_ROWS: 
    #now 40 > 30 from our example, so on-time rate: 28/40 = 70%
            rate = df.loc[donor, "ot"].mean()
        else: #else, if fewer than 30 donors(min_donor_rows), no improvement
            rate = df.loc[broken, "ot"].mean()      # assume no improvement

        now = df.loc[broken, "ot"].mean()
        projected_on_time += n_broken * rate
        #suppose if 4 of 10 broken orders were on time,then current rate is 40%
        #projection estimates 10* 70% from our example=7 on-time among 10. 
        #combine 7 with the donor's 28 actual on time orders-> 35 of projected total
        rows.append({
            "remaining_problems": ", ".join(
                c.replace("_", " ") for c, v in zip(OTHER_FLAGS, combo) if v
            ) or "none", 
#what other problems it had,for true,false,true our example, they are far wh and no api carrier
            "orders_held": n_broken, #it's order count->10
            "their_on_time_pct": round(100 * now, 1), #actual historical ontime rate-> 40
            "donor_on_time_pct": round(100 * rate, 1), 
#comparison rate used to estimate what that group might achive after addresses are resolved-> 28 of 40nwere on time, 70%
            "donor_rows": n_donor, #donor count->40
            "recovered": round(n_broken * (rate - now)),  #recovery is the estimated additional on time deliveries
            #estimated recovery in this example is 10* (70-40) = 3
        })

    projected = 100.0 * projected_on_time / n_all 
#estimated number of on-time delivery across whole dataset into a percent
    rows.sort(key=lambda r: -r["recovered"])
#sorts the group so that the groups with the most estimated recovered deliveries appear 
# first.
    return {
        "current_on_time_pct": round(baseline, 1), #actual on time percentage
        "projected_on_time_pct": round(projected, 1), 
        #estimated on time percentage after resolving incomplete addresses
        "gain_pts": round(projected - baseline, 1),
        #projected percent-current percent in percent points
        "deliveries_recovered": sum(r["recovered"] for r in rows),
        #the sum of all the estimated recovered deliveries across all groups
        "cohorts": rows,
    #the sorted list of per group details added earlier with rows.append()
    }


# ------------------------------------------------------------------ decision
def decide(screened: dict, quantified: dict, vendor_price_usd: int = 680_000,
           vendor_gain_pts: float = 2.0) -> dict:
    """What to do, and how it compares with the routing vendor."""
    gain = quantified["gain_pts"]
    return {
        "action": "hold incomplete-address orders at ingestion",
#hold incomplete address orders when they enter the system
        "orders_affected": screened["would_hold"], #the number of incomplete address orders
        "gain_pts": gain, #records estimated improvement
        "vendor_gain_pts": vendor_gain_pts, 
    #records vendor's assumed improvmenet,defaulting to 2 points
        "times_better_than_vendor": round(gain / vendor_gain_pts, 1)
    #divides estimated gain by vendor gain. 
    #if vendor gain is 0, returns none instead of dividing by zero
        if vendor_gain_pts else None,
        "vendor_cost_per_point_usd": round(vendor_price_usd / vendor_gain_pts)
        #divides vendor price by it's gain and rounds to nearest dollar. 
        # the default price is 680000. 
        # if vendor gain is 0, this is also none
        if vendor_gain_pts else None,
    }


NARRATIVE_PROMPT = """You are writing two sentences for an operations report.

Figures, all measured from 48,340 delivery records:
- orders with an incomplete address at ingestion: {would_hold}
- of those, how many were delivered late: {held_that_went_late}
- on-time rate today: {current}%
- projected on-time rate if those addresses were resolved before dispatch: {projected}%
- deliveries recovered: {recovered}

Write two plain sentences stating what the figures show and what to do.
Use only the figures given. Do not add numbers, causes or recommendations
that are not in the list above.
"""


def narrate(screened: dict, quantified: dict, llm=None) -> str:
    """Plain-language summary. The model writes words, never numbers."""
    fallback = (
        f"{screened['would_hold']:,} of {screened['orders_screened']:,} orders "
        f"were ingested with an incomplete address, and "
        f"{screened['held_that_went_late']:,} of those were delivered late. "
        f"Resolving the address before dispatch projects on-time delivery from "
        f"{quantified['current_on_time_pct']}% to "
        f"{quantified['projected_on_time_pct']}%, recovering "
        f"{quantified['deliveries_recovered']:,} deliveries."
    )
    if llm is None:
        return fallback
    try:
        response = llm.invoke(NARRATIVE_PROMPT.format(
            would_hold=screened["would_hold"],
            held_that_went_late=screened["held_that_went_late"],
            current=quantified["current_on_time_pct"],
            projected=quantified["projected_on_time_pct"],
            recovered=quantified["deliveries_recovered"],
        ))
        return response_text(response.content).strip()
    except Exception:
        return fallback
