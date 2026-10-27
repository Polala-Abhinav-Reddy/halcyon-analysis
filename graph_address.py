"""The address intake agent as a LangGraph, over the real delivery records.

    load -> screen -> quantify -> narrate -> report

Reads data/deliveries.csv. Invents nothing: every figure traces to a row.
Runs with no API key -- the narrative falls back to a deterministic sentence.
"""
import json
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from agents.address_agent import decide, narrate, quantify, screen
from llm import get_llm, provider_name
from pipeline.db import connect, profile

OUT_FILE = "results_address_agent.json"


class State(TypedDict, total=False):
    con: object        # the open database
    profile: dict      # what was loaded
    screened: dict     # which orders would be held
    quantified: dict   # what the hold recovers
    decision: dict     # the action, priced against the vendor
    narrative: str     # two sentences in plain language
    report: dict       # everything, ready for the dashboard


def load(state: State) -> dict:
    con = connect()
    return {"con": con, "profile": profile(con)}


def screen_node(state: State) -> dict:
    return {"screened": screen(state["con"])}


def quantify_node(state: State) -> dict:
    q = quantify(state["con"])
    return {"quantified": q, "decision": decide(state["screened"], q)}


def narrate_node(state: State) -> dict:
    return {
        "narrative": narrate(state["screened"], state["quantified"], get_llm())
    }


def report(state: State) -> dict:
    out = {
        "agent": "address_intake",
        "source": "data/deliveries.csv",
        "narrative_written_by": provider_name(),
        "profile": state["profile"],
        "screening": state["screened"],
        "projection": state["quantified"],
        "decision": state["decision"],
        "narrative": state["narrative"],
    }
    with open(OUT_FILE, "w") as fh:
        json.dump(out, fh, indent=2)
    return {"report": out}


def build():
    g = StateGraph(State)
    g.add_node("load", load)
    g.add_node("screen", screen_node)
    g.add_node("quantify", quantify_node)
    g.add_node("narrate", narrate_node)
    g.add_node("report", report)

    g.add_edge(START, "load")
    g.add_edge("load", "screen")
    g.add_edge("screen", "quantify")
    g.add_edge("quantify", "narrate")
    g.add_edge("narrate", "report")
    g.add_edge("report", END)
    return g.compile()


if __name__ == "__main__":
    final = build().invoke({})
    s, q, d = final["screened"], final["quantified"], final["decision"]

    print(f"\nSource: {final['profile']['n_deliveries']:,} deliveries, "
          f"{final['profile']['first_order'][:10]} to "
          f"{final['profile']['last_order'][:10]}")

    print(f"\nSCREENING")
    print(f"  would hold          : {s['would_hold']:,} orders "
          f"({s['pct_of_book_held']}% of the book)")
    print(f"  of those, went late : {s['held_that_went_late']:,}")

    print(f"\n  by channel:")
    for c in s["by_channel"]:
        print(f"    {c['channel']:6} {c['would_hold']:6,} of {c['orders']:6,} "
              f"= {c['pct_held']:5.1f}% held")

    print(f"\n  worst clients (50+ orders):")
    for c in s["worst_clients"][:5]:
        flag = "  <- threatening to leave" if c["account_status"] != "active" else ""
        print(f"    {c['client_id']}  {c['would_hold']:4,} of {c['orders']:4,} "
              f"= {c['pct_held']:5.1f}%{flag}")

    print(f"\nPROJECTION  (cohort-matched, donors from the real data)")
    print(f"    {'remaining problems':38} {'held':>6} {'now':>7} {'donor':>7} "
          f"{'donors':>7} {'recovered':>10}")
    for r in q["cohorts"]:
        print(f"    {r['remaining_problems']:38} {r['orders_held']:6,} "
              f"{r['their_on_time_pct']:6.1f}% {r['donor_on_time_pct']:6.1f}% "
              f"{r['donor_rows']:7,} {r['recovered']:10,}")
    print(f"    {'TOTAL':38} {s['would_hold']:6,} {'':7} {'':7} {'':7} "
          f"{q['deliveries_recovered']:10,}")

    print(f"\n  on-time {q['current_on_time_pct']}% -> "
          f"{q['projected_on_time_pct']}%  (+{q['gain_pts']} pts)")

    print(f"\nDECISION")
    print(f"  {d['action']}")
    print(f"  worth {d['gain_pts']} pts vs the vendor's {d['vendor_gain_pts']} "
          f"-- {d['times_better_than_vendor']}x, at no licence cost")
    print(f"  the vendor's own gain costs "
          f"${d['vendor_cost_per_point_usd']:,} per point")

    print(f"\nNARRATIVE  (by {final['report']['narrative_written_by']})")
    print(f"  {final['narrative']}")
    print(f"\nwrote {OUT_FILE}")
