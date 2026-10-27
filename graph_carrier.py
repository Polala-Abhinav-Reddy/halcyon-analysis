"""The carrier selection agent as a LangGraph, over the real delivery records.

    load -> screen -> quantify -> cost -> narrate -> report

Reads data/deliveries.csv and data/carriers.csv. Invents nothing.
Runs with no API key -- the narrative falls back to a deterministic sentence.
"""
import json
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from agents.carrier_agent import cost, decide, narrate, quantify, screen
from llm import get_llm, provider_name
from pipeline.db import connect, profile

OUT_FILE = "results_carrier_agent.json"


class State(TypedDict, total=False):
    con: object
    profile: dict
    screened: dict
    quantified: dict
    costed: dict
    decision: dict
    narrative: str
    report: dict


def load(state: State) -> dict:
    con = connect()
    return {"con": con, "profile": profile(con)}


def screen_node(state: State) -> dict:
    return {"screened": screen(state["con"])}


def quantify_node(state: State) -> dict:
    return {"quantified": quantify(state["con"])}


def cost_node(state: State) -> dict:
    costed = cost(state["con"], state["screened"])
    return {
        "costed": costed,
        "decision": decide(state["screened"], state["quantified"], costed),
    }


def narrate_node(state: State) -> dict:
    return {"narrative": narrate(state["screened"], state["quantified"],
                                 state["costed"], get_llm())}


def report(state: State) -> dict:
    out = {
        "agent": "carrier_selection",
        "source": "data/deliveries.csv + data/carriers.csv",
        "narrative_written_by": provider_name(),
        "profile": state["profile"],
        "screening": state["screened"],
        "projection": state["quantified"],
        "cost": state["costed"],
        "decision": state["decision"],
        "narrative": state["narrative"],
    }
    with open(OUT_FILE, "w") as fh:
        json.dump(out, fh, indent=2)
    return {"report": out}


def build():
    g = StateGraph(State)
    for name, fn in [("load", load), ("screen", screen_node),
                     ("quantify", quantify_node), ("cost", cost_node),
                     ("narrate", narrate_node), ("report", report)]:
        g.add_node(name, fn)

    g.add_edge(START, "load")
    g.add_edge("load", "screen")
    g.add_edge("screen", "quantify")
    g.add_edge("quantify", "cost")
    g.add_edge("cost", "narrate")
    g.add_edge("narrate", "report")
    g.add_edge("report", END)
    return g.compile()


if __name__ == "__main__":
    final = build().invoke({})
    s, q, c, d = (final["screened"], final["quantified"],
                  final["costed"], final["decision"])

    print(f"\nSource: {final['profile']['n_deliveries']:,} deliveries")

    print(f"\nSCREENING")
    print(f"  on a carrier with no event feed : {s['on_non_api_carrier']:,} "
          f"({s['pct_of_book_non_api']}% of the book)")
    print(f"  of those, went late             : {s['non_api_that_went_late']:,}")
    print(f"  average rate, no feed / API     : "
          f"${s['avg_rate_non_api_usd']} / ${s['avg_rate_api_usd']}")

    print(f"\n  {'carrier':8} {'API':4} {'pref':5} {'feed':13} {'orders':>7} "
          f"{'rate':>7} {'on-time':>8} {'transit':>8}")
    for r in s["by_carrier"]:
        print(f"  {r['carrier_id']:8} {r['has_api']:4} {r['preferred_supplier']:5} "
              f"{r['event_feed']:13} {r['orders']:7,} ${r['avg_rate_usd']:6.2f} "
              f"{r['on_time_pct']:7.1f}% {r['transit_days']:8.2f}")

    print(f"\n  best and worst carrier by lane (100+ shipments):")
    print(f"    {'region':12} {'best':>16} {'worst':>16} {'spread':>8}")
    for r in s["by_lane"]:
        print(f"    {r['region']:12} {r['best_carrier']:>8} "
              f"{r['best_on_time_pct']:6.1f}% {r['worst_carrier']:>8} "
              f"{r['worst_on_time_pct']:6.1f}% {r['spread_pts']:7.1f}")

    print(f"\nPROJECTION  (cohort-matched, donors from the real data)")
    print(f"    {'remaining problems':34} {'moved':>6} {'now':>7} {'donor':>7} "
          f"{'donors':>7} {'recovered':>10}")
    for r in q["cohorts"]:
        print(f"    {r['remaining_problems']:34} {r['orders_reassigned']:6,} "
              f"{r['their_on_time_pct']:6.1f}% {r['donor_on_time_pct']:6.1f}% "
              f"{r['donor_rows']:7,} {r['recovered']:10,}")
    print(f"    {'TOTAL':34} {s['on_non_api_carrier']:6,} {'':7} {'':7} {'':7} "
          f"{q['deliveries_recovered']:10,}")
    print(f"\n  on-time {q['current_on_time_pct']}% -> "
          f"{q['projected_on_time_pct']}%  (+{q['gain_pts']} pts)")

    print(f"\nCOST OF THE FIX")
    print(f"  rate uplift per shipment   : "
          f"${c['rate_uplift_per_shipment_usd']}")
    print(f"  extra freight, annualised  : "
          f"${c['extra_freight_annualised_usd']:,}")
    print(f"  penalties + redelivery, 6mo: ${c['failure_cost_6mo_usd']:,} "
          f"({c['sla_penalty_count']:,} breaches)")
    print(f"  failure cost is {c['failure_cost_times_freight_uplift']}x the "
          f"freight uplift")

    print(f"\nDECISION")
    for a in d["actions"]:
        print(f"  - {a}")
    print(f"\n  worth {d['gain_pts']} pts vs the vendor's {d['vendor_gain_pts']} "
          f"-- {d['times_better_than_vendor']}x")
    print(f"  this fix : ${d['this_fix_cost_per_point_usd']:,} per point per year")
    print(f"  vendor   : ${d['vendor_cost_per_point_usd']:,} per point")

    print(f"\nNARRATIVE  (by {final['report']['narrative_written_by']})")
    print(f"  {final['narrative']}")
    print(f"\nwrote {OUT_FILE}")
