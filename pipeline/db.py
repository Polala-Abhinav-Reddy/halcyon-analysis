"""Database connection and the flag view everything else is built on.

One job: open the real CSVs and produce `f` -- deliveries with the four risk
flags attached. Every flag is derived from a column that exists in the data.
"""
import duckdb

# Carriers with no API integration, per carriers.csv.
NO_API_CARRIERS = ("D", "E", "F")

# Route excess above this fraction is where on-time actually degrades.
# Derived from the data: buckets below 10% all sit at ~73% on-time, the
# >10% bucket drops to 66.5%.
ROUTE_EXCESS_THRESHOLD = 0.10

# Pick lists are compiled at 08:00 at all five warehouses, per warehouses.csv.
PICK_CUTOFF_HOUR = 8


def connect(data_dir: str = "data") -> duckdb.DuckDBPyConnection:
    """In-memory DB with views over the raw CSVs, plus the flag view `f`."""
    con = duckdb.connect()

    def view(name: str, filename: str) -> None:
        con.sql(
            f"""CREATE VIEW {name} AS
                SELECT * FROM read_csv('{data_dir}/{filename}',
                                       header=true, sample_size=-1)"""
        )

    view("d", "deliveries.csv")
    view("cl", "clients.csv")
    view("ca", "carriers.csv")
    view("wh", "warehouses.csv")
    view("sp", "sla_penalties.csv")
    view("cm", "complaints.csv")
    view("cs", "customer_success_time_study.csv")

    carriers = ", ".join(f"'{c}'" for c in NO_API_CARRIERS)
    con.sql(
        f"""CREATE VIEW f AS SELECT *,
              (address_status = 'incomplete')                    AS bad_address,
              (nearest_warehouse_id <> fulfilment_warehouse_id)  AS far_warehouse,
              (hour(order_received_ts) >= {PICK_CUTOFF_HOUR})    AS missed_cutoff,
              (carrier_id IN ({carriers}))                       AS no_api_carrier,
              (actual_route_km IS NOT NULL
                 AND (actual_route_km - planned_route_km) / planned_route_km
                     > {ROUTE_EXCESS_THRESHOLD})                 AS route_excess,
              (on_time = 'Y')                                    AS ot
            FROM d"""
    )
    return con


def profile(con) -> dict:
    """Row counts, date range, headline on-time rate, data-quality notes."""
    row = con.sql(
        """SELECT COUNT(*),
                  MIN(order_received_ts), MAX(order_received_ts),
                  100.0 * SUM(CASE WHEN ot THEN 1 ELSE 0 END) / COUNT(*),
                  SUM(CASE WHEN NOT ot THEN 1 ELSE 0 END),
                  SUM(CASE WHEN actual_route_km IS NULL THEN 1 ELSE 0 END)
           FROM f"""
    ).fetchall()[0]
    return {
        "n_deliveries": int(row[0]),
        "first_order": str(row[1]),
        "last_order": str(row[2]),
        "on_time_pct": round(float(row[3]), 1),
        "late_deliveries": int(row[4]),
        "rows_missing_actual_route_km": int(row[5]),
    }
