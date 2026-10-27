"""Show the cohort arithmetic behind each cumulative level, and test the
method out-of-sample."""
import duckdb, itertools, pandas as pd

con = duckdb.connect()
con.sql("CREATE VIEW d AS SELECT * FROM read_csv('data/deliveries.csv', header=true, sample_size=-1)")
con.sql("""CREATE VIEW f AS SELECT *,
  (address_status='incomplete')                     AS A,
  (nearest_warehouse_id <> fulfilment_warehouse_id) AS W,
  (hour(order_received_ts) >= 8)                    AS C,
  (carrier_id IN ('D','E','F'))                     AS K,
  (on_time='Y')                                     AS ot,
  order_received_ts                                 AS ts
FROM d""")

df = con.sql("SELECT A, W, C, K, ot, ts FROM f").df()
FLAGS = ['A', 'W', 'C', 'K']
NAME = {'A': 'bad address', 'W': 'far warehouse', 'C': 'missed cutoff', 'K': 'no-API carrier'}
N = len(df)
base = 100 * df.ot.sum() / N


def level(fixed, frame=None, verbose=True):
    """Projected on-time % if every flag in `fixed` were removed."""
    data = df if frame is None else frame
    n_all = len(data)
    clean = ~data[list(fixed)].any(axis=1)
    rest = [c for c in FLAGS if c not in fixed]

    rows, total = [], 0.0
    for combo in itertools.product([False, True], repeat=len(rest)):
        sel = data.index == data.index
        for c, v in zip(rest, combo):
            sel = sel & (data[c] == v)
        n = int(sel.sum())
        if n == 0:
            continue
        donor = sel & clean
        n_don = int(donor.sum())
        rate = data.loc[donor, 'ot'].mean() if n_don >= 30 else data.loc[sel, 'ot'].mean()
        now = data.loc[sel, 'ot'].mean()
        total += n * rate
        rows.append({
            'leftover': ", ".join(NAME[c] for c, v in zip(rest, combo) if v) or "none",
            'n': n, 'now_pct': round(100 * now, 1), 'donor_pct': round(100 * rate, 1),
            'n_donor': n_don, 'rescued': round(n * (rate - now)),
        })
    if verbose:
        print(pd.DataFrame(rows).to_string(index=False))
    return 100 * total / n_all


print("=" * 90)
print("LEVEL 1  fix: bad address")
print("=" * 90)
l1 = level({'A'})
print(f"\n  projected on-time = {l1:.1f}%   (from {base:.1f}%, +{l1-base:.1f})\n")

print("=" * 90)
print("LEVEL 2  fix: bad address + far warehouse")
print("=" * 90)
l2 = level({'A', 'W'})
print(f"\n  projected on-time = {l2:.1f}%   (+{l2-l1:.1f} over level 1)\n")

print("=" * 90)
print("LEVEL 3  fix: bad address + far warehouse + no-API carrier")
print("=" * 90)
l3 = level({'A', 'W', 'K'})
print(f"\n  projected on-time = {l3:.1f}%   (+{l3-l2:.1f} over level 2)\n")

print("=" * 90)
print("LEVEL 4  fix: all four")
print("=" * 90)
l4 = level(set(FLAGS))
print(f"\n  projected on-time = {l4:.1f}%   (+{l4-l3:.1f} over level 3)")
print("  NOTE: with all four fixed there is only one cohort left, so this level")
print("  is BY CONSTRUCTION the clean-cohort rate. It is not an independent check.\n")

# ---------------------------------------------------------------- validation
print("=" * 90)
print("OUT-OF-SAMPLE TEST  (train on Apr-Jun, predict Jul-Sep)")
print("=" * 90)
df['half'] = (df.ts >= '2026-07-01').map({False: 'train', True: 'test'})
train, test = df[df.half == 'train'], df[df.half == 'test']
print(f"train rows {len(train)},  test rows {len(test)}\n")

rows = []
for combo in itertools.product([False, True], repeat=4):
    m_tr = train.index == train.index
    m_te = test.index == test.index
    for c, v in zip(FLAGS, combo):
        m_tr = m_tr & (train[c] == v)
        m_te = m_te & (test[c] == v)
    n_tr, n_te = int(m_tr.sum()), int(m_te.sum())
    if n_tr < 30 or n_te < 30:
        continue
    pred = 100 * train.loc[m_tr, 'ot'].mean()
    act = 100 * test.loc[m_te, 'ot'].mean()
    rows.append({
        'cohort': "".join(c for c, v in zip(FLAGS, combo) if v) or "clean",
        'n_test': n_te, 'predicted_pct': round(pred, 1),
        'actual_pct': round(act, 1), 'error_pts': round(act - pred, 1),
    })
out = pd.DataFrame(rows)
print(out.to_string(index=False))
print(f"\n  mean absolute error = {out.error_pts.abs().mean():.2f} points")
print(f"  max absolute error  = {out.error_pts.abs().max():.2f} points")
