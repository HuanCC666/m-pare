#!/usr/bin/env python3
"""Generate proposal rate vs acceptance rate tradeoff plots for the full benchmark.

Creates two figures:
1. Simple scatter plot with Pareto frontier
2. Bubble chart with success rate as bubble size, with Pareto frontier

Usage:
    python scripts/plots/plot_proposal_acceptance_tradeoff.py --results-dir RESULTS_DIR --output-dir OUTPUT_DIR
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt

# Model display names
MODEL_DISPLAY_NAMES = {
    "claude-4.5-sonnet": "Claude 4.5 Sonnet",
    "gpt-5": "GPT-5",
    "gemini-3-pro": "Gemini 3 Pro",
    "gemini-3-flash": "Gemini 3 Flash",
    "qwen-3-4b-it": "Qwen3-4B",
    "gemma-3-4b-it": "Gemma3-4B",
    "llama-3.2-3b-it": "Llama3.2-3B",
}

MODEL_MARKERS = {
    "claude-4.5-sonnet": "o",
    "gpt-5": "P",
    "gemini-3-pro": "X",
    "gemini-3-flash": "v",
    "qwen-3-4b-it": "s",
    "gemma-3-4b-it": "^",
    "llama-3.2-3b-it": "D",
}

MODEL_COLORS = {
    "claude-4.5-sonnet": "#0072B2",  # blue
    "gpt-5": "#D55E00",  # vermillion
    "gemini-3-pro": "#56B4E9",  # sky blue
    "gemini-3-flash": "#332288",  # indigo
    "qwen-3-4b-it": "#E69F00",  # orange
    "gemma-3-4b-it": "#009E73",  # green
    "llama-3.2-3b-it": "#CC79A7",  # pink
}


def extract_model_name(proactive_model: str) -> str:
    """Extract the observe model name from the proactive_model field."""
    # Pattern: observe-execute_{obs_model}_{exec_model}
    parts = proactive_model.replace("observe-execute_", "").split("_")
    return parts[0] if parts else proactive_model


def load_model_data(results_dir: Path) -> list[dict[str, float | str]]:
    """Load per-model aggregate metrics from combined_result.json."""
    combined_file = results_dir / "combined_result.json"
    if not combined_file.exists():
        raise FileNotFoundError(f"Combined results file not found: {combined_file}")

    with open(combined_file) as f:
        data = json.load(f)

    models = []
    for config in data["per_config_results"]:
        model = extract_model_name(config["proactive_model"])
        models.append({
            "model": model,
            "proposal_rate": config["aggregate_proposal_rate"] * 100,
            "acceptance_rate": min(config["aggregate_acceptance_rate"], 1.0) * 100,
            "success_rate": config["success_rate"],
        })
    return models


def load_ablation_data(
    results_dir: Path,
) -> tuple[dict[str, list[tuple[float, float, float]]], dict[str, list[tuple[float, float, float]]]]:
    """Load ablation data grouped by noise dimension.

    Returns:
        Tuple of (tfp_data, enmi_data) where each maps model name to
        list of (noise_value, proposal_rate, acceptance_rate) sorted by noise_value.
    """
    combined_file = results_dir / "combined_result.json"
    if not combined_file.exists():
        raise FileNotFoundError(f"Combined results file not found: {combined_file}")

    with open(combined_file) as f:
        data = json.load(f)

    tfp_data: dict[str, list[tuple[float, float, float]]] = {}
    enmi_data: dict[str, list[tuple[float, float, float]]] = {}

    for config in data["per_config_results"]:
        model = extract_model_name(config["proactive_model"])
        epm = config.get("num_env_events_per_minute", 0)
        tfp = config.get("tool_failure_probability", 0.0)
        pr = config["aggregate_proposal_rate"] * 100
        ar = min(config["aggregate_acceptance_rate"], 1.0) * 100

        # TFP curve: only include configs with no env noise
        if epm == 0:
            if model not in tfp_data:
                tfp_data[model] = []
            tfp_data[model].append((tfp, pr, ar))

        # ENMI curve: only include configs with no tool failure
        if tfp == 0.0:
            if model not in enmi_data:
                enmi_data[model] = []
            enmi_data[model].append((float(epm), pr, ar))

    # Sort by noise value
    for model in tfp_data:
        tfp_data[model].sort()
    for model in enmi_data:
        enmi_data[model].sort()

    return tfp_data, enmi_data


def compute_pareto_frontier(
    points: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    """Compute the Pareto frontier (maximize both x and y).

    Args:
        points: List of (x, y, label) tuples.

    Returns:
        Pareto-optimal points sorted by x.
    """
    # Sort by x ascending
    sorted_points = sorted(points, key=lambda p: p[0])

    frontier = []
    max_y = -float("inf")

    # Walk from right to left: a point is on the frontier if its y
    # is higher than any point with a higher x value
    for x, y, label in reversed(sorted_points):
        if y >= max_y:
            frontier.append((x, y, label))
            max_y = y

    # Return sorted by x ascending
    return list(reversed(frontier))


def plot_scatter(
    models: list[dict[str, float | str]],
    output_path: Path | None = None,
) -> None:
    """Simple scatter plot with Pareto frontier."""
    fig, ax = plt.subplots(figsize=(8, 6))

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.size"] = 10

    # Plot each model with text labels
    for m in models:
        model = str(m["model"])
        display_name = MODEL_DISPLAY_NAMES.get(model, model)
        marker = MODEL_MARKERS.get(model, "o")
        color = MODEL_COLORS.get(model, "#333333")
        pr = float(m["proposal_rate"])
        ar = float(m["acceptance_rate"])

        ax.scatter(
            pr,
            ar,
            marker=marker,
            color=color,
            s=100,
            zorder=5,
            edgecolors=color,
            linewidths=1.5,
        )

        # Place label with model-specific offsets to avoid overlap
        offsets = {
            "claude-4.5-sonnet": (0.0, 2.0),
            "gemini-3-pro": (0.0, 2.0),
            "gemini-3-flash": (-4.0, -4.5),
            "gpt-5": (-1.0, 2.5),
            "qwen-3-4b-it": (0.5, 2.0),
            "llama-3.2-3b-it": (-3.0, -4.5),
            "gemma-3-4b-it": (0.5, 2.0),
        }
        dx, dy = offsets.get(model, (0.5, 2.0))
        ax.annotate(
            display_name,
            (pr, ar),
            xytext=(pr + dx, ar + dy),
            fontsize=9,
            color=color,
            fontweight="bold",
        )

    # Compute and plot Pareto frontier
    points = [(float(m["proposal_rate"]), float(m["acceptance_rate"]), str(m["model"])) for m in models]
    frontier = compute_pareto_frontier(points)

    if len(frontier) > 1:
        frontier_x = [p[0] for p in frontier]
        frontier_y = [p[1] for p in frontier]
        ax.plot(
            frontier_x,
            frontier_y,
            color="#999999",
            linestyle="--",
            linewidth=1.5,
            alpha=0.7,
            zorder=2,
        )

    ax.set_xlabel("Proposal Rate (%)", fontsize=12)
    ax.set_ylabel("Acceptance Rate (%)", fontsize=12)
    ax.set_xlim(0, 32)
    ax.set_ylim(0, 105)

    ax.grid(True, linestyle="--", alpha=0.7, color="#cccccc")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#666666")
        spine.set_linewidth(1.2)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def plot_bubble(
    models: list[dict[str, float | str]],
    output_path: Path | None = None,
) -> None:
    """Bubble chart with success rate as bubble size, plus Pareto frontier."""
    fig, ax = plt.subplots(figsize=(8, 6))

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.size"] = 10

    # Scale success rate to bubble size (min 50, max 600)
    success_rates = [float(m["success_rate"]) for m in models]
    sr_min = min(success_rates)
    sr_max = max(success_rates)
    sr_range = sr_max - sr_min if sr_max > sr_min else 1.0

    # Plot each model
    for m in models:
        model = str(m["model"])
        display_name = MODEL_DISPLAY_NAMES.get(model, model)
        color = MODEL_COLORS.get(model, "#333333")
        pr = float(m["proposal_rate"])
        ar = float(m["acceptance_rate"])
        sr = float(m["success_rate"])

        # Scale bubble size
        size = 80 + 520 * ((sr - sr_min) / sr_range)

        ax.scatter(
            pr,
            ar,
            s=size,
            color=color,
            alpha=0.7,
            zorder=5,
            edgecolors=color,
            linewidths=1.5,
            label=f"{display_name} ({sr:.1f}%)",
        )

    # Compute and plot Pareto frontier
    points = [(float(m["proposal_rate"]), float(m["acceptance_rate"]), str(m["model"])) for m in models]
    frontier = compute_pareto_frontier(points)

    if len(frontier) > 1:
        frontier_x = [p[0] for p in frontier]
        frontier_y = [p[1] for p in frontier]
        ax.plot(
            frontier_x,
            frontier_y,
            color="#999999",
            linestyle="--",
            linewidth=1.5,
            alpha=0.7,
            zorder=2,
        )

    ax.set_xlabel("Proposal Rate (%)", fontsize=12)
    ax.set_ylabel("Acceptance Rate (%)", fontsize=12)
    ax.set_xlim(0, None)
    ax.set_ylim(0, 105)

    ax.grid(True, linestyle="--", alpha=0.7, color="#cccccc")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#666666")
        spine.set_linewidth(1.2)

    # Add size legend for success rate
    legend_sizes = [sr_min, (sr_min + sr_max) / 2, sr_max]
    legend_handles = []
    for sr_val in legend_sizes:
        size = 80 + 520 * ((sr_val - sr_min) / sr_range)
        handle = ax.scatter(
            [],
            [],
            s=size,
            color="#999999",
            alpha=0.5,
            edgecolors="#666666",
            linewidths=1,
            label=f"SR={sr_val:.0f}%",
        )
        legend_handles.append(handle)

    # Model legend
    model_legend = ax.legend(
        loc="lower left",
        frameon=True,
        fancybox=False,
        edgecolor="#666666",
        fontsize=9,
    )

    # Add size legend separately
    size_legend = ax.legend(
        handles=legend_handles,
        title="Success Rate",
        loc="upper right",
        frameon=True,
        fancybox=False,
        edgecolor="#666666",
        fontsize=8,
        title_fontsize=9,
    )
    ax.add_artist(model_legend)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def plot_noise_tradeoff_curve(
    noise_data: dict[str, list[tuple[float, float, float]]],
    noise_label: str,
    output_path: Path | None = None,
) -> None:
    """Plot proposal rate vs acceptance rate curves across noise levels.

    Each model gets a line connecting its operating points as noise increases.

    Args:
        noise_data: Maps model name to list of (noise_value, proposal_rate, acceptance_rate).
        noise_label: Label for the noise dimension (e.g., "Tool Failure Prob." or "Env. Noise (epm)").
        output_path: Path to save the figure.
    """
    fig, ax = plt.subplots(figsize=(8, 6))

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.size"] = 10

    for model in sorted(noise_data.keys()):
        points = noise_data[model]
        display_name = MODEL_DISPLAY_NAMES.get(model, model)
        marker = MODEL_MARKERS.get(model, "o")
        color = MODEL_COLORS.get(model, "#333333")

        prs = [p[1] for p in points]
        ars = [p[2] for p in points]
        noise_vals = [p[0] for p in points]

        # Draw line connecting points
        ax.plot(
            prs,
            ars,
            color=color,
            linewidth=1.5,
            alpha=0.7,
            zorder=3,
        )

        # Draw markers at each noise level
        for i, (nv, pr, ar) in enumerate(points):
            ax.scatter(
                pr,
                ar,
                marker=marker,
                color=color,
                s=80,
                zorder=5,
                edgecolors=color,
                linewidths=1.5,
            )

        # Label at the first point (baseline, lowest noise)
        pr0, ar0 = prs[0], ars[0]
        offsets = {
            "claude-4.5-sonnet": (0.0, 2.0),
            "gemini-3-pro": (0.0, 2.0),
            "gemini-3-flash": (-4.0, -4.5),
            "gpt-5": (-1.0, 2.5),
            "qwen-3-4b-it": (0.5, 2.0),
            "llama-3.2-3b-it": (-3.0, -4.5),
            "gemma-3-4b-it": (0.5, 2.0),
        }
        dx, dy = offsets.get(model, (0.5, 2.0))
        ax.annotate(
            display_name,
            (pr0, ar0),
            xytext=(pr0 + dx, ar0 + dy),
            fontsize=9,
            color=color,
            fontweight="bold",
        )

        # Annotate noise values at first and last point
        ax.annotate(
            f"{noise_vals[0]:.1f}"
            if isinstance(noise_vals[0], float) and noise_vals[0] != int(noise_vals[0])
            else f"{int(noise_vals[0])}",
            (prs[0], ars[0]),
            xytext=(3, -12),
            textcoords="offset points",
            fontsize=7,
            color=color,
            alpha=0.7,
        )
        ax.annotate(
            f"{noise_vals[-1]:.1f}"
            if isinstance(noise_vals[-1], float) and noise_vals[-1] != int(noise_vals[-1])
            else f"{int(noise_vals[-1])}",
            (prs[-1], ars[-1]),
            xytext=(3, -12),
            textcoords="offset points",
            fontsize=7,
            color=color,
            alpha=0.7,
        )

    ax.set_xlabel("Proposal Rate (%)", fontsize=12)
    ax.set_ylabel("Acceptance Rate (%)", fontsize=12)
    ax.set_xlim(0, 35)
    ax.set_ylim(0, 105)

    ax.grid(True, linestyle="--", alpha=0.7, color="#cccccc")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#666666")
        spine.set_linewidth(1.2)

    # Add noise dimension annotation
    ax.text(
        0.98,
        0.02,
        f"Noise: {noise_label}",
        transform=ax.transAxes,
        fontsize=10,
        ha="right",
        va="bottom",
        color="#666666",
        style="italic",
    )

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate proposal rate vs acceptance rate tradeoff plots.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory containing combined_result.json (full benchmark)",
    )
    parser.add_argument(
        "--ablation-results-dir",
        type=Path,
        default=None,
        help="Directory containing ablation combined_result.json (for noise curves)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for figures (default: same as results-dir)",
    )

    args = parser.parse_args()

    output_dir = args.output_dir or args.results_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Plot 1: Scatter from full benchmark
    print(f"Loading full benchmark results from: {args.results_dir}")
    models = load_model_data(args.results_dir)

    print(f"\nFound {len(models)} models:")
    for m in models:
        print(
            f"  {m['model']}: PR={m['proposal_rate']:.1f}%, AR={m['acceptance_rate']:.1f}%, SR={m['success_rate']:.1f}%"
        )

    points = [(float(m["proposal_rate"]), float(m["acceptance_rate"]), str(m["model"])) for m in models]
    frontier = compute_pareto_frontier(points)
    print(f"\nPareto frontier ({len(frontier)} points):")
    for x, y, label in frontier:
        print(f"  {label}: PR={x:.1f}%, AR={y:.1f}%")

    plot_scatter(models, output_dir / "proposal_acceptance_scatter.pdf")

    # Plots 2-3: Noise tradeoff curves from ablation
    if args.ablation_results_dir:
        print(f"\nLoading ablation results from: {args.ablation_results_dir}")
        tfp_data, enmi_data = load_ablation_data(args.ablation_results_dir)

        print(f"\nTFP curves: {len(tfp_data)} models, {[len(v) for v in tfp_data.values()]} points each")
        plot_noise_tradeoff_curve(tfp_data, "Tool Failure Prob.", output_dir / "tradeoff_curve_tfp.pdf")

        print(f"ENMI curves: {len(enmi_data)} models, {[len(v) for v in enmi_data.values()]} points each")
        plot_noise_tradeoff_curve(enmi_data, "Env. Noise (epm)", output_dir / "tradeoff_curve_enmi.pdf")

    print("\nDone!")


if __name__ == "__main__":
    main()
