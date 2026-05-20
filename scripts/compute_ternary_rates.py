#!/usr/bin/env python3
"""Compute ternary decision rates (accept/reject/gather_context/truncated) from benchmark traces.

Scans all proposals across the full trace (no execute cutoff) and classifies
the user agent's immediate response. Produces per-config and combined reports
with mean +/- SEM statistics matching the benchmark reporting pipeline.

Usage:
    uv run python scripts/compute_ternary_rates.py --traces-dir TRACES_DIR
    uv run python scripts/compute_ternary_rates.py --traces-dir traces/paper_benchmark_full_user_gpt-5-mini_mt_10_umi_1_omi_5_emi_10
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

ACCEPT_TOOLS = {"PASAgentUserInterface__accept_proposal", "PAREAgentUserInterface__accept_proposal"}
REJECT_TOOLS = {"PASAgentUserInterface__reject_proposal", "PAREAgentUserInterface__reject_proposal"}
DECISION_TOOLS = ACCEPT_TOOLS | REJECT_TOOLS

# Schema for per-run ternary results DataFrame
TERNARY_SCHEMA: dict[str, type[pl.DataType]] = {
    "scenario_id": pl.Utf8,
    "run_number": pl.Int64,
    "config": pl.Utf8,
    "model": pl.Utf8,
    "total_proposals": pl.Int64,
    "accept": pl.Int64,
    "reject": pl.Int64,
    "gather_context": pl.Int64,
    "truncated": pl.Int64,
    "gather_to_accept": pl.Int64,
    "gather_to_reject": pl.Int64,
    "gather_to_truncated": pl.Int64,
}


def _identify_agents(logs: list[dict[str, Any]]) -> dict[str, str]:
    """Identify user, observe, and execute agent IDs from system prompts."""
    agents: dict[str, str] = {}
    for log in logs[:50]:
        if log.get("log_type") != "system_prompt":
            continue
        content = log.get("content", "")
        agent_id = log.get("agent_id", "")
        if not isinstance(content, str) or not agent_id:
            continue
        if "proactive assistant that monitors" in content:
            agents["observe"] = agent_id
        elif "<meta_task_description>" in content:
            agents["user"] = agent_id
        elif "execute" in content.lower() and "task" in content.lower():
            agents["execute"] = agent_id
    return agents


def _classify_decisions(
    logs: list[dict[str, Any]],
    observe_id: str,
    user_id: str,
) -> list[dict[str, str | bool | None]]:
    """Find all observe-agent proposals and classify the user's response.

    Returns list of dicts with keys: decision, final_decision.
    decision is one of: accept, reject, gather_context, truncated.
    final_decision is True (accept), False (reject), or None (truncated/unresolved).
    """
    proposal_indices = []
    for i, log in enumerate(logs):
        if log.get("agent_id") == observe_id and "send_message_to_user" in str(log.get("tool_name", "")):
            proposal_indices.append(i)

    decisions: list[dict[str, str | bool | None]] = []
    for pi, prop_idx in enumerate(proposal_indices):
        scan_end = proposal_indices[pi + 1] if pi + 1 < len(proposal_indices) else len(logs)

        first_user_tool = None
        final_decision_bool: bool | None = None

        for j in range(prop_idx + 1, scan_end):
            log = logs[j]
            if log.get("agent_id") != user_id:
                continue
            tool_name = log.get("tool_name", "")
            if not tool_name or log.get("log_type") != "tool_call":
                continue

            if first_user_tool is None:
                first_user_tool = tool_name

            if tool_name in ACCEPT_TOOLS:
                final_decision_bool = True
                if first_user_tool in DECISION_TOOLS:
                    break
            elif tool_name in REJECT_TOOLS:
                final_decision_bool = False
                if first_user_tool in DECISION_TOOLS:
                    break

        if first_user_tool is None:
            decisions.append({"decision": "truncated", "final_decision": None})
            continue

        if first_user_tool in ACCEPT_TOOLS:
            decisions.append({"decision": "accept", "final_decision": True})
        elif first_user_tool in REJECT_TOOLS:
            decisions.append({"decision": "reject", "final_decision": False})
        else:
            decisions.append({"decision": "gather_context", "final_decision": final_decision_bool})

    return decisions


def _extract_run_info(trace_path: Path) -> tuple[str, int]:
    """Extract scenario_id and run_number from trace filename."""
    stem = trace_path.stem
    if "_run_" in stem:
        parts = stem.rsplit("_run_", 1)
        return parts[0], int(parts[1])
    return stem, 1


def analyze_trace(trace_path: Path, config: str, model: str) -> dict[str, Any] | None:
    """Analyze a single trace file and return per-run ternary counts."""
    try:
        with open(trace_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    logs = data.get("world_logs", [])
    parsed: list[dict[str, Any]] = []
    for log in logs:
        if isinstance(log, str):
            try:
                parsed.append(json.loads(log))
            except json.JSONDecodeError:
                continue
        else:
            parsed.append(log)

    agents = _identify_agents(parsed)
    observe_id = agents.get("observe")
    user_id = agents.get("user")
    if not observe_id or not user_id:
        return None

    decisions = _classify_decisions(parsed, observe_id, user_id)
    scenario_id, run_number = _extract_run_info(trace_path)

    row: dict[str, Any] = {
        "scenario_id": scenario_id,
        "run_number": run_number,
        "config": config,
        "model": model,
        "total_proposals": len(decisions),
        "accept": 0,
        "reject": 0,
        "gather_context": 0,
        "truncated": 0,
        "gather_to_accept": 0,
        "gather_to_reject": 0,
        "gather_to_truncated": 0,
    }

    for d in decisions:
        dec = d["decision"]
        final = d["final_decision"]
        if dec == "accept":
            row["accept"] += 1
        elif dec == "reject":
            row["reject"] += 1
        elif dec == "truncated":
            row["truncated"] += 1
        elif dec == "gather_context":
            row["gather_context"] += 1
            if final is True:
                row["gather_to_accept"] += 1
            elif final is False:
                row["gather_to_reject"] += 1
            else:
                row["gather_to_truncated"] += 1

    return row


def build_dataframe(traces_dir: Path) -> pl.DataFrame:
    """Build a Polars DataFrame with per-run ternary counts for all configs."""
    rows: list[dict[str, Any]] = []

    for config_dir in sorted(traces_dir.iterdir()):
        if not config_dir.is_dir():
            continue

        config_name = config_dir.name
        model = config_name.replace("obs_", "").split("_exec_")[0]

        for trace_file in sorted(config_dir.glob("*.json")):
            result = analyze_trace(trace_file, config_name, model)
            if result is not None:
                rows.append(result)

    if not rows:
        return pl.DataFrame(schema=TERNARY_SCHEMA)

    return pl.DataFrame(rows, schema=TERNARY_SCHEMA)


def _rate_with_sem(
    df: pl.DataFrame,
    numerator_col: str,
    denominator_col: str,
) -> dict[str, float]:
    """Compute aggregate rate with run-level STD and SEM.

    Groups by run_number, computes per-run rate, then STD(ddof=1) and SEM.
    Matches the benchmark pipeline pattern.
    """
    total_num = df.select(numerator_col).sum().item()
    total_den = df.select(denominator_col).sum().item()
    aggregate = total_num / total_den if total_den > 0 else 0.0

    run_numbers = df.select("run_number").unique().to_series().sort()
    run_rates = []
    for run_num in run_numbers:
        run_df = df.filter(pl.col("run_number") == run_num)
        if not run_df.is_empty():
            r_num = run_df.select(numerator_col).sum().item()
            r_den = run_df.select(denominator_col).sum().item()
            if r_den > 0:
                run_rates.append(r_num / r_den)

    std = float(np.std(run_rates, ddof=1)) if len(run_rates) > 1 else 0.0
    sem = std / float(np.sqrt(len(run_rates))) if len(run_rates) > 1 else 0.0

    return {"value": aggregate, "std": std, "sem": sem}


def compute_config_stats(config_df: pl.DataFrame) -> dict[str, Any]:
    """Compute ternary stats for a single config DataFrame."""
    if config_df.is_empty():
        return _empty_stats()

    totals = {
        col: config_df.select(col).sum().item()
        for col in [
            "total_proposals",
            "accept",
            "reject",
            "gather_context",
            "truncated",
            "gather_to_accept",
            "gather_to_reject",
            "gather_to_truncated",
        ]
    }

    stats: dict[str, Any] = {
        "total_traces": len(config_df),
        "total_runs": config_df.select("run_number").n_unique(),
        **totals,
        "accept_rate": _rate_with_sem(config_df, "accept", "total_proposals"),
        "reject_rate": _rate_with_sem(config_df, "reject", "total_proposals"),
        "gather_rate": _rate_with_sem(config_df, "gather_context", "total_proposals"),
        "truncated_rate": _rate_with_sem(config_df, "truncated", "total_proposals"),
    }

    # Gather context breakdown rates (denominator = gather_context count)
    gather_df = config_df.filter(pl.col("gather_context") > 0)
    if not gather_df.is_empty() and totals["gather_context"] > 0:
        stats["gather_to_accept_rate"] = _rate_with_sem(gather_df, "gather_to_accept", "gather_context")
        stats["gather_to_reject_rate"] = _rate_with_sem(gather_df, "gather_to_reject", "gather_context")
        stats["gather_to_truncated_rate"] = _rate_with_sem(gather_df, "gather_to_truncated", "gather_context")
    else:
        empty = {"value": 0.0, "std": 0.0, "sem": 0.0}
        stats["gather_to_accept_rate"] = empty
        stats["gather_to_reject_rate"] = empty
        stats["gather_to_truncated_rate"] = empty

    return stats


def _empty_stats() -> dict[str, Any]:
    """Return empty stats structure."""
    empty = {"value": 0.0, "std": 0.0, "sem": 0.0}
    return {
        "total_traces": 0,
        "total_runs": 0,
        "total_proposals": 0,
        "accept": 0,
        "reject": 0,
        "gather_context": 0,
        "truncated": 0,
        "gather_to_accept": 0,
        "gather_to_reject": 0,
        "gather_to_truncated": 0,
        "accept_rate": empty,
        "reject_rate": empty,
        "gather_rate": empty,
        "truncated_rate": empty,
        "gather_to_accept_rate": empty,
        "gather_to_reject_rate": empty,
        "gather_to_truncated_rate": empty,
    }


def _fmt(rate: dict[str, float]) -> str:
    """Format rate as 'value% +/- sem% (STD: std%)'."""
    return f"{rate['value'] * 100:.1f}% +/- {rate['sem'] * 100:.1f}% (STD: {rate['std'] * 100:.1f}%)"


def generate_config_report(model: str, stats: dict[str, Any]) -> str:
    """Generate text report for a single config."""
    report = f"\n=== Config: {model} ===\n"
    report += "\n=== Metadata ===\n"
    report += f"  - Traces: {stats['total_traces']} ({stats['total_runs']} runs)\n"
    report += f"  - Total proposals: {stats['total_proposals']}\n"

    report += "\n=== Ternary Decision Rates ===\n"
    report += f"  - Accept: {stats['accept']} -- {_fmt(stats['accept_rate'])}\n"
    report += f"  - Reject: {stats['reject']} -- {_fmt(stats['reject_rate'])}\n"
    report += f"  - Gather context: {stats['gather_context']} -- {_fmt(stats['gather_rate'])}\n"
    report += f"  - Truncated: {stats['truncated']} -- {_fmt(stats['truncated_rate'])}\n"

    if stats["gather_context"] > 0:
        report += "\n=== Gather Context Breakdown ===\n"
        report += f"  - G -> Accept: {stats['gather_to_accept']} -- {_fmt(stats['gather_to_accept_rate'])}\n"
        report += f"  - G -> Reject: {stats['gather_to_reject']} -- {_fmt(stats['gather_to_reject_rate'])}\n"
        report += f"  - G -> Truncated: {stats['gather_to_truncated']} -- {_fmt(stats['gather_to_truncated_rate'])}\n"

    return report


def analyze_benchmark(traces_dir: Path, output_dir: Path | None = None) -> None:
    """Analyze all model configs in a benchmark traces directory."""
    results_dir = output_dir or traces_dir.parent / traces_dir.name.replace("traces/", "results/")
    results_dir = results_dir / "ternary"
    results_dir.mkdir(parents=True, exist_ok=True)

    print("Building DataFrame from traces...")
    df = build_dataframe(traces_dir)
    if df.is_empty():
        print("No valid traces found.")
        return

    print(f"Total rows: {len(df)}")

    configs = df.select("config").unique().to_series().sort().to_list()
    all_stats: dict[str, dict[str, Any]] = {}

    for config_name in configs:
        config_df = df.filter(pl.col("config") == config_name)
        model = config_df.select("model").row(0)[0]
        stats = compute_config_stats(config_df)
        all_stats[model] = stats

        # Save per-config JSON (DataFrame rows + stats)
        config_json = {
            "model": model,
            "config": config_name,
            "stats": stats,
            "per_run": config_df.to_dicts(),
        }
        with open(results_dir / f"{config_name}_ternary_result.json", "w") as f:
            json.dump(config_json, f, indent=2, default=str)

        # Save per-config report
        report = generate_config_report(model, stats)
        with open(results_dir / f"{config_name}_ternary_report.txt", "w") as f:
            f.write(report)

    # Combined report
    timestamp = datetime.now(tz=UTC).isoformat()
    combined_report = "\n=== PARE Ternary Decision Report ===\n"
    combined_report += f"Generated: {timestamp}\n"
    combined_report += f"Traces: {traces_dir}\n"
    for model in sorted(all_stats.keys()):
        combined_report += generate_config_report(model, all_stats[model])

    with open(results_dir / "combined_ternary_report.txt", "w") as f:
        f.write(combined_report)

    # Combined JSON
    combined_json = {
        "metadata": {"timestamp": timestamp, "traces_dir": str(traces_dir)},
        "per_config_results": {model: stats for model, stats in sorted(all_stats.items())},
    }
    with open(results_dir / "combined_ternary_result.json", "w") as f:
        json.dump(combined_json, f, indent=2, default=str)

    # Print summary tables
    print(f"\n{'Model':<22} {'Total':>6} {'Accept':>14} {'Reject':>14} {'Gather':>14} {'Truncated':>14}")
    print("-" * 90)
    for model in sorted(all_stats.keys()):
        s = all_stats[model]
        t = s["total_proposals"]
        print(
            f"{model:<22} {t:>6} "
            f"{s['accept']:>5} ({s['accept_rate']['value'] * 100:>4.1f}%) "
            f"{s['reject']:>5} ({s['reject_rate']['value'] * 100:>4.1f}%) "
            f"{s['gather_context']:>5} ({s['gather_rate']['value'] * 100:>4.1f}%) "
            f"{s['truncated']:>5} ({s['truncated_rate']['value'] * 100:>4.1f}%)"
        )

    print(f"\n{'Model':<22} {'Gather':>7} {'G->Acc':>14} {'G->Rej':>14} {'G->Trunc':>14}")
    print("-" * 70)
    for model in sorted(all_stats.keys()):
        s = all_stats[model]
        g = s["gather_context"]
        if g == 0:
            continue
        print(
            f"{model:<22} {g:>7} "
            f"{s['gather_to_accept']:>5} ({s['gather_to_accept_rate']['value'] * 100:>4.1f}%) "
            f"{s['gather_to_reject']:>5} ({s['gather_to_reject_rate']['value'] * 100:>4.1f}%) "
            f"{s['gather_to_truncated']:>5} ({s['gather_to_truncated_rate']['value'] * 100:>4.1f}%)"
        )

    print(f"\nResults saved to: {results_dir}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Compute ternary decision rates from benchmark traces.")
    parser.add_argument(
        "--traces-dir",
        type=Path,
        required=True,
        help="Benchmark traces directory (contains model config subdirectories)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for results (default: derived from traces-dir)",
    )
    args = parser.parse_args()

    print(f"Analyzing traces in: {args.traces_dir}\n")
    analyze_benchmark(args.traces_dir, args.output_dir)


if __name__ == "__main__":
    main()
