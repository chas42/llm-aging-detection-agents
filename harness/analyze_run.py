"""Trend analysis of one diagnostic run, or a before/after comparison of two runs.

For every time series in a run (server samples, windowed client metrics, instrumentation
snapshots) it reports: Mann-Kendall (original + Hamed-Rao modified for autocorrelation),
Sen's slope in units per hour, and first/last values. Results go to <run>/analysis/.

    python harness/analyze_run.py runs/<run>                    # single run
    python harness/analyze_run.py runs/<before> --compare runs/<after>   # comparison
Options: --window 60 (client aggregation, s), --warmup 60 (s discarded at the start)
"""
import argparse
import json
import math
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pymannkendall as mk  # noqa: E402

# pymannkendall divides by a zero autocovariance on near-constant series; the result is still handled.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pymannkendall")

ALPHA = 0.05
MAX_POINTS = 1500  # Sen's slope is O(n^2); longer series are evenly downsampled
MB = 1024 * 1024


# ----------------------------------------------------------------------------- loading

def load_series(run: Path, window: float, warmup: float) -> dict[str, pd.Series]:
    """Return {name: Series indexed by elapsed hours}."""
    meta = json.loads((run / "meta.json").read_text())
    t0 = meta.get("start") or 0.0  # client clock
    # Two-machine runs: server-side timestamps (monitor, instrumentation) are on the server clock;
    # offset_s = server - client, so subtracting it maps them onto the client clock.
    srv_offset = meta.get("clock", {}).get("offset_s", 0.0)
    series: dict[str, pd.Series] = {}

    def add(name: str, ts: pd.Series, values: pd.Series, server_side: bool = False) -> None:
        hours = (ts - (srv_offset if server_side else 0.0) - t0) / 3600.0
        s = pd.Series(values.to_numpy(dtype=float), index=hours.to_numpy()).dropna()
        s = s[s.index >= warmup / 3600.0]
        if len(s) >= 3:
            series[name] = s

    mon = run / "monitor.csv"
    if mon.exists():
        df = pd.read_csv(mon)
        add("server.rss_mb", df.ts, df.rss_bytes / MB, True)
        add("server.uss_mb", df.ts, df.uss_bytes / MB, True)
        add("server.num_fds", df.ts, df.num_fds, True)
        add("server.num_threads", df.ts, df.num_threads, True)
        add("server.cpu_percent", df.ts, df.cpu_percent, True)
        add("server.watched_files_mb", df.ts, df.watched_bytes / MB, True)

    lg = run / "client_monitor.csv"  # the load generator itself: rules out client saturation
    if lg.exists():
        df = pd.read_csv(lg)
        add("loadgen.cpu_percent", df.ts, df.cpu_percent)
        add("loadgen.rss_mb", df.ts, df.rss_bytes / MB)

    cli = run / "client.csv"
    if cli.exists():
        df = pd.read_csv(cli)
        df["win"] = ((df.ts - t0) // window).astype(int)
        df["ok"] = df.status.between(200, 399)
        for ep, g in df.groupby("endpoint"):
            agg = g.groupby("win").agg(
                ts=("ts", "min"),
                p50=("latency_ms", "median"),
                p95=("latency_ms", lambda x: np.percentile(x, 95)),
                resp_bytes=("resp_bytes", "mean"),
                n=("latency_ms", "size"),
                ok=("ok", "mean"),
            )
            agg = agg.iloc[1:-1] if len(agg) > 3 else agg  # drop partial first/last windows
            ts = (agg.index.to_series() * window + t0)
            add(f"client.{ep}.latency_p50_ms", ts, agg.p50)
            add(f"client.{ep}.latency_p95_ms", ts, agg.p95)
            add(f"client.{ep}.resp_bytes_mean", ts, agg.resp_bytes)
            add(f"client.{ep}.throughput_rps", ts, agg.n / window)
            add(f"client.{ep}.error_rate", ts, 1 - agg.ok)

    ins = run / "instrumentation.jsonl"
    if ins.exists():
        rows = []
        with open(ins) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "snapshot":
                    rows.append({"ts": rec["ts"], **rec.get("metrics", {})})
        if rows:
            df = pd.DataFrame(rows)
            for col in df.columns:
                if col != "ts" and pd.api.types.is_numeric_dtype(df[col]):
                    add(f"instr.{col}", df.ts, df[col], True)
    return series


# ----------------------------------------------------------------------------- statistics

def sen_slope_per_hour(s: pd.Series) -> float:
    if len(s) > MAX_POINTS:
        s = s.iloc[np.linspace(0, len(s) - 1, MAX_POINTS).astype(int)]
    x, y = s.index.to_numpy(), s.to_numpy()
    i, j = np.triu_indices(len(x), k=1)
    dx = x[j] - x[i]
    mask = dx > 0
    return float(np.median((y[j] - y[i])[mask] / dx[mask])) if mask.any() else float("nan")


def trend(s: pd.Series) -> dict:
    y = s.to_numpy()
    res = {"n": int(len(y)), "first": float(y[0]), "last": float(y[-1]),
           "median": float(np.median(y)), "sen_slope_per_h": sen_slope_per_hour(s)}
    # Effect size: change implied by the Sen slope over the whole series, relative to its median.
    span_h = float(s.index[-1] - s.index[0])
    res["sen_change_pct"] = (res["sen_slope_per_h"] * span_h / abs(res["median"]) * 100
                             if res["median"] else float("nan"))
    if np.allclose(y, y[0]):
        res.update(mk_p=1.0, mk_hr_p=1.0, trend="constant")
        return res
    try:
        r = mk.original_test(y)
        res["mk_p"], res["mk_tau"] = float(r.p), float(r.Tau)
    except Exception as e:  # pragma: no cover - defensive
        res["mk_p"], res["mk_error"] = float("nan"), str(e)
    try:
        res["mk_hr_p"] = float(mk.hamed_rao_modification_test(y).p)
    except Exception:
        res["mk_hr_p"] = float("nan")
    p = res["mk_hr_p"] if not math.isnan(res.get("mk_hr_p", float("nan"))) else res["mk_p"]
    slope = res["sen_slope_per_h"]
    res["trend"] = ("increasing" if slope > 0 else "decreasing") if p < ALPHA and slope != 0 else "no trend"
    return res


def analyze(run: Path, window: float, warmup: float) -> dict[str, dict]:
    series = load_series(run, window, warmup)
    if not series:
        print(f"[analyze] WARNING: no series with >= 3 points after the {warmup:.0f}s warm-up in {run.name}; "
              "the run is too short for this --warmup/--window", file=sys.stderr)
    out_dir = run / "analysis"
    out_dir.mkdir(exist_ok=True)
    results = {name: trend(s) for name, s in sorted(series.items())}
    (out_dir / "summary.json").write_text(json.dumps(results, indent=2))
    (out_dir / "summary.md").write_text(render_table(run.name, results))
    plot(series, out_dir / "series.png", run.name)
    return results


# ----------------------------------------------------------------------------- output

def fmt(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.3g}" if abs(v) < 1e4 else f"{v:.3e}"


def render_table(title: str, results: dict[str, dict]) -> str:
    lines = [f"# Trend summary: {title}", "",
             "| series | n | first | last | Sen slope /h | Sen change % | MK p | MK-HR p | trend |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        lines.append(f"| {name} | {r['n']} | {fmt(r['first'])} | {fmt(r['last'])} | "
                     f"{fmt(r['sen_slope_per_h'])} | {fmt(r.get('sen_change_pct'))} | {fmt(r.get('mk_p'))} | {fmt(r.get('mk_hr_p'))} | {r['trend']} |")
    return "\n".join(lines) + "\n"


def plot(series: dict[str, pd.Series], path: Path, title: str, other: dict[str, pd.Series] | None = None,
         labels: tuple[str, str] = ("run", "")) -> None:
    names = sorted(set(series) | set(other or {}))
    if not names:
        return
    cols = 3
    rows = math.ceil(len(names) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 2.8 * rows), squeeze=False)
    for ax, name in zip(axes.flat, names):
        for data, label, color in ((series, labels[0], "#3b6fb6"), (other or {}, labels[1], "#d0662b")):
            if name in data:
                ax.plot(data[name].index, data[name].values, lw=0.9, color=color, label=label or None)
        ax.set_title(name, fontsize=8)
        ax.set_xlabel("hours", fontsize=7)
        ax.tick_params(labelsize=7)
        if other:
            ax.legend(fontsize=6)
    for ax in list(axes.flat)[len(names):]:
        ax.axis("off")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def compare(before: Path, after: Path, window: float, warmup: float) -> Path:
    rb, ra = analyze(before, window, warmup), analyze(after, window, warmup)
    sb, sa = load_series(before, window, warmup), load_series(after, window, warmup)
    out_dir = after / "analysis"
    lines = [f"# Comparison: {before.name} (before) vs {after.name} (after)", "",
             "| series | before slope /h | before trend | after slope /h | after trend | before median | after median |",
             "|---|---|---|---|---|---|---|"]
    for name in sorted(set(rb) | set(ra)):
        b, a = rb.get(name, {}), ra.get(name, {})
        lines.append(f"| {name} | {fmt(b.get('sen_slope_per_h'))} | {b.get('trend', '-')} | "
                     f"{fmt(a.get('sen_slope_per_h'))} | {a.get('trend', '-')} | "
                     f"{fmt(b.get('median'))} | {fmt(a.get('median'))} |")
    path = out_dir / f"comparison_vs_{before.name}.md"
    path.write_text("\n".join(lines) + "\n")
    plot(sb, out_dir / f"comparison_vs_{before.name}.png", f"{before.name} vs {after.name}", sa,
         labels=("before", "after"))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--compare", type=Path, default=None, help="the 'after' run to compare against")
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--warmup", type=float, default=60.0)
    args = ap.parse_args()
    if args.compare:
        print(compare(args.run, args.compare, args.window, args.warmup).read_text())
    else:
        analyze(args.run, args.window, args.warmup)
        print((args.run / "analysis" / "summary.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
