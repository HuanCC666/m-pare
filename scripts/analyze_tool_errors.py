r"""Count tool-call error classes by proactive-agent role, normalized to LLM-call counts.

For each trace we:
  1. Identify the agent_id of the observe and execute agents by matching the
     ``system_prompt`` content against known prompt fingerprints.
  2. Count llm_output entries per role -> denominator (number of LLM calls).
  3. Count error entries per role using either the ``agent`` field directly
     (e.g. ``observe_base_agent``) or by mapping via agent_id.

Output: per-model, per-role percentage of LLM calls that errored with a format
error or an unrecognized tool error.

Usage:
    uv run python scripts/analyze_tool_errors.py <experiment_dir> --models m1 m2 ...

Example:
    uv run python scripts/analyze_tool_errors.py \
        traces/paper_benchmark_full_user_gpt-5-mini_mt_10_umi_1_omi_5_emi_10 \
        --models claude-4.5-sonnet gpt-5 glm-5.1 kimi-k2.6 \
        --out tool_errors.json
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Error classes emitted by the agent runtime. These are stable strings written
# to world_logs entries under the ``error`` field by Meta-ARE / PARE agents.
ERROR_CLASSES = (
    "UnavailableToolAgentError",  # model called a tool that does not exist
    "FormatError",  # parser found an output that does not match the ReAct format
    "InvalidActionAgentError",  # output is missing the Action: token entirely
    "JsonParsingAgentError",  # action_input JSON could not be parsed
    "JsonExecutionAgentError",  # tool executed but raised at runtime; not a format / hallucination issue
)

# Categories used by the release graphic.
FORMAT_ERROR_CLASSES = (
    "FormatError",
    "InvalidActionAgentError",
    "JsonParsingAgentError",
)
INVALID_TOOL_ERROR_CLASSES = ("UnavailableToolAgentError",)

# Proactive-agent roles.
ROLES = ("observe", "execute")

# Map ``agent`` string on error entries to a role.
AGENT_NAME_TO_ROLE = {
    "observe_base_agent": "observe",
    "execute_base_agent": "execute",
}

# Heuristic system-prompt fingerprints to map agent_id -> role within one trace.
ROLE_PROMPT_FINGERPRINTS = {
    "observe": "proactive assistant that monitors user actions",
    "execute": "proactive assistant executing an approved task",
}


@dataclass
class ModelErrorReport:
    """Per-model tally of trace counts and call-normalized error rates."""

    model: str
    trace_count: int = 0
    # Legacy per-trace tallies (kept for the JSON dump).
    traces_with_class: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ERROR_CLASSES, 0))
    total_errors_by_class: Counter[str] = field(default_factory=Counter)
    sample_exceptions: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in ERROR_CLASSES})
    traces_with_any_format_error: int = 0
    # Per-role call counts and error counts.
    role_llm_calls: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ROLES, 0))
    role_errors_by_class: dict[str, Counter[str]] = field(default_factory=lambda: {role: Counter() for role in ROLES})


def _iter_world_log_entries(trace_path: Path) -> Iterable[dict[str, object]]:
    """Yield each parsed world_log entry from a single trace JSON file."""
    try:
        with trace_path.open() as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Could not read trace {trace_path}: {exc}")
        return
    raw_logs = data.get("world_logs", [])
    if not isinstance(raw_logs, list):
        return
    for raw in raw_logs:
        if not isinstance(raw, str):
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def _build_agent_id_to_role(entries: list[dict[str, object]]) -> dict[str, str]:
    """Map agent_id to role by inspecting system_prompt content within a trace."""
    mapping: dict[str, str] = {}
    for entry in entries:
        if entry.get("log_type") != "system_prompt":
            continue
        aid = entry.get("agent_id")
        content = entry.get("content", "")
        if not isinstance(aid, str) or not isinstance(content, str):
            continue
        if aid in mapping:
            continue
        for role, fingerprint in ROLE_PROMPT_FINGERPRINTS.items():
            if fingerprint in content:
                mapping[aid] = role
                break
    return mapping


def _resolve_role_for_error(
    entry: dict[str, object],
    agent_id_to_role: dict[str, str],
) -> str | None:
    """Pick observe/execute for an error entry.

    Prefers the ``agent`` field (e.g. ``observe_base_agent``); falls back to
    looking up the entry's agent_id against the system-prompt-derived map.
    Returns None for entries that belong to the user simulator or unknown agents.
    """
    agent = entry.get("agent")
    if isinstance(agent, str) and agent in AGENT_NAME_TO_ROLE:
        return AGENT_NAME_TO_ROLE[agent]
    aid = entry.get("agent_id")
    if isinstance(aid, str) and aid in agent_id_to_role:
        return agent_id_to_role[aid]
    return None


def analyze_model_dir(model: str, model_dir: Path, sample_limit: int = 3) -> ModelErrorReport:  # noqa: C901
    """Tally error counts and LLM-call counts across all trace JSON files."""
    report = ModelErrorReport(model=model)
    if not model_dir.is_dir():
        logger.warning(f"Model dir not found: {model_dir}")
        return report

    for trace_path in sorted(model_dir.glob("*.json")):
        report.trace_count += 1
        entries = list(_iter_world_log_entries(trace_path))
        agent_id_to_role = _build_agent_id_to_role(entries)

        seen_classes_in_trace: set[str] = set()
        for entry in entries:
            log_type = entry.get("log_type")
            aid_raw = entry.get("agent_id")
            aid: str | None = aid_raw if isinstance(aid_raw, str) else None

            if log_type == "llm_output" and aid is not None:
                role = agent_id_to_role.get(aid)
                if role in ROLES:
                    report.role_llm_calls[role] += 1
                continue

            if log_type != "error":
                continue
            err_class = entry.get("error")
            if not isinstance(err_class, str) or err_class not in ERROR_CLASSES:
                continue
            report.total_errors_by_class[err_class] += 1
            seen_classes_in_trace.add(err_class)
            samples = report.sample_exceptions[err_class]
            if len(samples) < sample_limit:
                exc_text = entry.get("exception") or ""
                if isinstance(exc_text, str) and exc_text:
                    samples.append(exc_text[:200])

            role = _resolve_role_for_error(entry, agent_id_to_role)
            if role in ROLES:
                report.role_errors_by_class[role][err_class] += 1

        for err_class in seen_classes_in_trace:
            report.traces_with_class[err_class] += 1
        if seen_classes_in_trace & set(FORMAT_ERROR_CLASSES):
            report.traces_with_any_format_error += 1

    return report


def _role_rate(report: ModelErrorReport, role: str, classes: tuple[str, ...]) -> tuple[int, int, float]:
    """Return (numerator, denominator, percentage) for one role + error category."""
    denominator = report.role_llm_calls[role]
    numerator = sum(report.role_errors_by_class[role][c] for c in classes)
    pct = (100.0 * numerator / denominator) if denominator else 0.0
    return numerator, denominator, pct


def print_report(reports: list[ModelErrorReport]) -> None:
    """Print per-role, call-normalized error rates."""
    name_w, col_w = 30, 26
    header = f"{'Metric':<{name_w}}" + "".join(f"{r.model:>{col_w}}" for r in reports)
    print(header)
    print("-" * len(header))
    print(f"{'(traces)':<{name_w}}" + "".join(f"{r.trace_count:>{col_w}}" for r in reports))
    print(f"{'(observe LLM calls)':<{name_w}}" + "".join(f"{r.role_llm_calls['observe']:>{col_w}}" for r in reports))
    print(f"{'(execute LLM calls)':<{name_w}}" + "".join(f"{r.role_llm_calls['execute']:>{col_w}}" for r in reports))
    print()

    for role in ROLES:
        print(f"--- {role} role ---")
        for label, classes in (
            ("format errors", FORMAT_ERROR_CLASSES),
            ("invalid tool errors", INVALID_TOOL_ERROR_CLASSES),
        ):
            row = f"  {label:<{name_w - 2}}"
            for r in reports:
                num, den, pct = _role_rate(r, role, classes)
                row += f"{pct:>6.2f}% ({num:>3}/{den:>4})   "
            print(row)
        print()


def write_json(reports: list[ModelErrorReport], out_path: Path) -> None:
    """Persist the report as JSON for downstream consumers."""
    payload = {
        "format_error_classes": list(FORMAT_ERROR_CLASSES),
        "invalid_tool_error_classes": list(INVALID_TOOL_ERROR_CLASSES),
        "roles": list(ROLES),
        "models": [
            {
                "model": r.model,
                "trace_count": r.trace_count,
                "traces_with_class": r.traces_with_class,
                "total_errors_by_class": dict(r.total_errors_by_class),
                "traces_with_any_format_error": r.traces_with_any_format_error,
                "role_llm_calls": r.role_llm_calls,
                "role_errors_by_class": {role: dict(counter) for role, counter in r.role_errors_by_class.items()},
                "role_rates": {
                    role: {
                        "format": _role_rate(r, role, FORMAT_ERROR_CLASSES)[2],
                        "invalid_tool": _role_rate(r, role, INVALID_TOOL_ERROR_CLASSES)[2],
                    }
                    for role in ROLES
                },
                "sample_exceptions": r.sample_exceptions,
            }
            for r in reports
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")


def main() -> None:
    """Parse CLI arguments and run the per-model error analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_dir", type=Path, help="Path to an experiment trace directory")
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model aliases (e.g. claude-4.5-sonnet gpt-5 glm-5.1 kimi-k2.6)",
    )
    parser.add_argument("--enmi", type=int, default=0, help="Env-noise events-per-min component (default 0)")
    parser.add_argument("--seed", type=int, default=42, help="Env-events seed component (default 42)")
    parser.add_argument("--tfp", type=str, default="0.0", help="Tool-failure probability component (default 0.0)")
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    reports = []
    for model in args.models:
        model_dir = args.experiment_dir / f"obs_{model}_exec_{model}_enmi_{args.enmi}_es_{args.seed}_tfp_{args.tfp}"
        reports.append(analyze_model_dir(model, model_dir))

    print_report(reports)
    if args.out:
        write_json(reports, args.out)


if __name__ == "__main__":
    main()
