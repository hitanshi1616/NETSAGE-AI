#!/usr/bin/env python3
"""
diagnosis.py
------------
NetSage AI — Phase 2: AI diagnosis layer.

For a given troubleshooting case, this module:
  1. Loads the system prompt from prompts/diagnose_prompt.md
  2. Builds a user message from the case's symptom, topology note,
     show-command evidence, and the deterministic rule-checker's findings
  3. Calls the Anthropic API and requires strict structured JSON output
  4. Validates the response (required fields, types)
  5. Checks that every claimed "observed_evidence" item is actually
     grounded in the case's show-command output (flags it if not)
  6. Saves the result to ai_diagnoses.csv

Design goals (student-readability first):
  - Every external failure mode (missing key, bad JSON, bad case id,
    network/API errors) has its own small, clearly-named exception class.
  - No function is more than ~40 lines; each does one job.
  - The AI is NEVER trusted blindly: human_review_required is always
    forced to True, and evidence grounding is checked, not assumed.
  - If the API key is missing, we say so plainly and stop. We never
    fabricate a diagnosis.

CLI usage:
    python diagnosis.py --case-id C001
    python diagnosis.py --all
    python diagnosis.py --case-id C001 --model claude-sonnet-4-5
    python diagnosis.py --case-id C001 --no-save
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional; env vars can also be set directly

# ---------------------------------------------------------------------------
# Paths (project layout: src/diagnosis.py lives one level below project root)
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(PROJECT_ROOT, "prompts", "diagnose_prompt.md")
CASES_CSV = os.path.join(PROJECT_ROOT, "cases", "cases.csv")
AI_DIAGNOSES_CSV = os.path.join(PROJECT_ROOT, "ai_diagnoses.csv")

# Make the Phase 1 rule checker importable regardless of current working directory.
sys.path.insert(0, os.path.join(PROJECT_ROOT, "checker"))
import checker as rule_checker  # noqa: E402  (Phase 1 deterministic checks)

# Model is configurable via .env / environment. Verify the current model
# catalog at https://docs.claude.com/en/docs/about-claude/models before
# relying on this default, since model names/availability change over time.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

REQUIRED_FIELDS = [
    "root_cause", "confidence", "osi_layer", "observed_evidence",
    "inference", "next_command", "fix_steps", "human_review_required",
]

CSV_FIELDNAMES = [
    "case_id", "timestamp", "model", "root_cause", "confidence", "osi_layer",
    "observed_evidence", "inference", "next_command", "fix_steps",
    "human_review_required", "evidence_grounded", "ungrounded_evidence_items",
    "grounding_note", "checker_findings_count", "source_type",
]

# Matches responsible_ai.py's VALID_SOURCE_TYPES exactly, so both CSVs use
# the same vocabulary. Every row diagnose_case() produces is a real API
# call, so it is always "Real AI Run" unless a caller (e.g. an offline
# demo-data generator) explicitly overrides it to "Evaluation/Demo".
SOURCE_TYPE_REAL_AI_RUN = "Real AI Run"
SOURCE_TYPE_EVALUATION_DEMO = "Evaluation/Demo"

# A small stopword list for the evidence-grounding word-overlap heuristic.
# Deliberately short and boring -- this isn't NLP, it's a sanity check.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but", "of",
    "to", "in", "on", "for", "with", "this", "that", "it", "its", "as",
    "at", "by", "from", "not", "no", "has", "have", "had", "be", "been",
    "which", "shows", "show", "line", "lines",
}


# ---------------------------------------------------------------------------
# Exceptions -- one per failure mode, so the CLI and tests can handle each
# distinctly and give the student a clear, specific message.
# ---------------------------------------------------------------------------

class DiagnosisError(Exception):
    """Base class for all diagnosis-layer errors."""


class MissingAPIKeyError(DiagnosisError):
    """Raised when ANTHROPIC_API_KEY is not set. We never fabricate a
    response in this situation -- the caller must supply a real key."""


class CaseNotFoundError(DiagnosisError):
    """Raised when the requested case_id does not exist in cases.csv."""


class APICallError(DiagnosisError):
    """Raised when the Anthropic API call itself fails (auth, rate limit,
    timeout, network, or an unexpected error), or the SDK isn't installed."""


class MalformedJSONError(DiagnosisError):
    """Raised when the AI's response text cannot be parsed as JSON."""

    def __init__(self, message: str, raw_text: str = ""):
        super().__init__(message)
        self.raw_text = raw_text


class ValidationError(DiagnosisError):
    """Raised when parsed JSON is missing required fields or has fields
    of the wrong type/shape."""


# ---------------------------------------------------------------------------
# Loading the prompt and the case
# ---------------------------------------------------------------------------

def load_system_prompt(prompt_path: str = PROMPT_PATH) -> str:
    """Extract the fenced ```...``` block under '## System Prompt' from
    prompts/diagnose_prompt.md."""
    if not os.path.exists(prompt_path):
        raise DiagnosisError(f"Prompt file not found at {prompt_path}")
    with open(prompt_path, encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"## System Prompt\s*```(.*?)```", text, re.DOTALL)
    if not match:
        raise DiagnosisError(
            f"Could not find a '## System Prompt' fenced block in {prompt_path}"
        )
    return match.group(1).strip()


def load_case(case_id: str, cases_csv: str = CASES_CSV) -> dict:
    """Load one case by ID from cases.csv. Raises CaseNotFoundError if the
    case_id doesn't exist, or DiagnosisError if cases.csv is missing."""
    if not os.path.exists(cases_csv):
        raise DiagnosisError(f"cases.csv not found at {cases_csv}")
    with open(cases_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("case_id") == case_id:
                return row
    raise CaseNotFoundError(
        f"case_id {case_id!r} was not found in {cases_csv}. "
        f"Run with --list to see valid case IDs."
    )


def list_case_ids(cases_csv: str = CASES_CSV) -> list[str]:
    if not os.path.exists(cases_csv):
        raise DiagnosisError(f"cases.csv not found at {cases_csv}")
    with open(cases_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["case_id"] for row in reader if row.get("case_id")]


def get_checker_findings(case: dict) -> list[dict]:
    """Run the Phase 1 deterministic rule checker against this case's
    structured network snapshot and return the findings as plain dicts."""
    try:
        snapshot = json.loads(case.get("network_snapshot_json") or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    findings = rule_checker.run_all_checks(snapshot)
    return [f.to_dict() for f in findings]


def build_user_message(case: dict, findings: list[dict]) -> str:
    """Assemble the per-case user message: symptom, topology, evidence,
    and the deterministic checker's findings (which the AI is told to
    verify rather than trust blindly)."""
    lines = [
        f"Symptom: {case.get('symptom', '')}",
        f"Topology: {case.get('topology_note', '')}",
        "Evidence:",
        (case.get("show_output") or "").strip(),
    ]
    if findings:
        lines.append("\nDeterministic rule-checker findings:")
        for f in findings:
            lines.append(f"- [{f['check']}] {f['message']}")
    else:
        lines.append(
            "\nDeterministic rule-checker findings: none (no structural "
            "rule violations detected -- this does not rule out other "
            "fault types like ACL/DNS/NAT issues)."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calling the Anthropic API
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Return the API key, or raise MissingAPIKeyError with a clear
    explanation. Never fabricate a key or a response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. NetSage AI requires a real "
            "Anthropic API key to produce a diagnosis -- it will never "
            "fabricate one. Copy .env.example to .env, add your key, "
            "and try again. (Get a key at https://console.anthropic.com/settings/keys)"
        )
    return api_key


def call_model(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    max_tokens: int = 1000,
    timeout: float = 60.0,
    max_retries: int = 2,
) -> str:
    """Call the Anthropic API and return the raw text response.

    Raises MissingAPIKeyError if no key is configured, or APICallError for
    any SDK-missing / auth / rate-limit / timeout / connection / unexpected
    error. Retries a couple of times (with a short backoff) on rate limits,
    timeouts, and transient connection errors, since those are often
    resolved by simply trying again.
    """
    api_key = get_api_key()

    try:
        import anthropic
    except ImportError as e:
        raise APICallError(
            "The 'anthropic' package is not installed. Run: "
            "pip install -r requirements.txt"
        ) from e

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    model = model or DEFAULT_MODEL

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
            if not text.strip():
                raise APICallError("The Anthropic API returned an empty response.")
            return text

        except anthropic.AuthenticationError as e:
            # Never worth retrying -- the key itself is wrong.
            raise APICallError(
                f"Authentication failed -- check that ANTHROPIC_API_KEY is "
                f"valid. ({e})"
            ) from e

        except (anthropic.RateLimitError, anthropic.APITimeoutError,
                anthropic.APIConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                backoff = 1.5 * (attempt + 1)
                time.sleep(backoff)
                continue
            kind = type(e).__name__
            raise APICallError(
                f"Anthropic API call failed after {max_retries + 1} attempts "
                f"({kind}): {e}. This is often transient -- try again in a "
                f"moment."
            ) from e

        except anthropic.APIStatusError as e:
            raise APICallError(
                f"Anthropic API returned an error (status {e.status_code}): "
                f"{e.message}"
            ) from e

        except Exception as e:  # noqa: BLE001 - deliberately broad, last resort
            raise APICallError(f"Unexpected error calling the Anthropic API: {e}") from e

    # Should not be reachable, but keeps type-checkers happy.
    raise APICallError(f"Anthropic API call failed: {last_error}")


# ---------------------------------------------------------------------------
# Parsing and validating the AI's JSON response
# ---------------------------------------------------------------------------

def extract_json_from_text(text: str) -> str:
    """The prompt asks for raw JSON with no code fences, but models
    sometimes add them anyway. Strip a fenced block if present; otherwise
    return the text as-is for json.loads to attempt directly."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def parse_ai_response(raw_text: str) -> dict:
    """Parse the AI's raw text response into a dict. Raises
    MalformedJSONError (carrying the raw text) if parsing fails or the
    top-level JSON value isn't an object."""
    candidate = extract_json_from_text(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise MalformedJSONError(
            f"AI response was not valid JSON: {e}", raw_text=raw_text
        ) from e
    if not isinstance(data, dict):
        raise MalformedJSONError(
            "AI response was valid JSON but not a JSON object (dict) as required.",
            raw_text=raw_text,
        )
    return data


def validate_diagnosis_schema(data: dict) -> dict:
    """Validate that `data` has all required fields with sane types, and
    return a normalized copy (confidence coerced to int 0-100,
    human_review_required forced to True). Raises ValidationError listing
    every problem found, so a student can fix a broken prompt/response in
    one pass instead of one field at a time."""
    problems: list[str] = []

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        problems.append(f"missing required field(s): {missing}")

    def _get(name, default=None):
        return data.get(name, default)

    # root_cause
    if "root_cause" in data and not isinstance(_get("root_cause"), str):
        problems.append("'root_cause' must be a string")

    # confidence: accept int or float, must be within [0, 100]
    normalized_confidence = None
    if "confidence" in data:
        conf = _get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            problems.append("'confidence' must be a number (0-100)")
        elif not (0 <= conf <= 100):
            problems.append(f"'confidence' must be between 0 and 100, got {conf}")
        else:
            normalized_confidence = int(round(conf))

    # osi_layer
    if "osi_layer" in data and not isinstance(_get("osi_layer"), str):
        problems.append("'osi_layer' must be a string")

    # observed_evidence: list of strings
    if "observed_evidence" in data:
        oe = _get("observed_evidence")
        if not isinstance(oe, list) or not all(isinstance(x, str) for x in oe):
            problems.append("'observed_evidence' must be a list of strings")

    # inference
    if "inference" in data and not isinstance(_get("inference"), str):
        problems.append("'inference' must be a string")

    # next_command
    if "next_command" in data and not isinstance(_get("next_command"), str):
        problems.append("'next_command' must be a string")

    # fix_steps: list of strings
    if "fix_steps" in data:
        fs = _get("fix_steps")
        if not isinstance(fs, list) or not all(isinstance(x, str) for x in fs):
            problems.append("'fix_steps' must be a list of strings")

    # human_review_required: must be present; type-check but we override below
    if "human_review_required" in data and not isinstance(_get("human_review_required"), bool):
        problems.append("'human_review_required' must be a boolean")

    if problems:
        raise ValidationError(
            "AI response failed schema validation: " + "; ".join(problems)
        )

    return {
        "root_cause": data["root_cause"],
        "confidence": normalized_confidence,
        "osi_layer": data["osi_layer"],
        "observed_evidence": data["observed_evidence"],
        "inference": data["inference"],
        "next_command": data["next_command"],
        "fix_steps": data["fix_steps"],
        # NetSage AI never lets the model waive human review, no matter
        # what it claims -- this is a hard product rule, not a suggestion.
        "human_review_required": True,
    }


# ---------------------------------------------------------------------------
# Evidence grounding validation
# ---------------------------------------------------------------------------

def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9.\-/]{3,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _grounding_check_one(evidence_item: str, show_output_lower: str) -> dict:
    """Check a single observed_evidence string against the case's
    show_output. Two signals are combined:
      1. Any IPv4-looking token in the evidence must appear verbatim in
         the show_output (a fabricated IP is a strong red flag).
      2. At least 40% of the "significant" words in the evidence item
         must appear somewhere in the show_output (a loose paraphrase
         check -- the AI is allowed to reword, not invent).
    """
    ip_tokens = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b", evidence_item)
    missing_ips = [ip for ip in ip_tokens if ip.split("/")[0] not in show_output_lower]

    sig_words = _significant_words(evidence_item)
    if sig_words:
        matched = sum(1 for w in sig_words if w in show_output_lower)
        overlap_ratio = matched / len(sig_words)
    else:
        overlap_ratio = 1.0  # nothing meaningful to check; don't false-flag

    grounded = (overlap_ratio >= 0.4) and not missing_ips
    return {
        "evidence": evidence_item,
        "grounded": grounded,
        "overlap_ratio": round(overlap_ratio, 2),
        "missing_ip_tokens": missing_ips,
    }


def check_evidence_grounding(
    observed_evidence: list[str], show_output: str
) -> tuple[bool, list[dict], str]:
    """Check every observed_evidence item against the case's show_output.

    Returns (overall_grounded, per_item_results, note).
    An empty observed_evidence list is treated as trivially "grounded"
    (nothing unsupported was claimed) but flagged with a note, since the
    prompt asks the model to always cite evidence when evidence exists.
    """
    if not observed_evidence:
        return True, [], "No observed_evidence was provided by the AI -- a human reviewer should verify the root cause manually."

    show_lower = (show_output or "").lower()
    results = [_grounding_check_one(item, show_lower) for item in observed_evidence]
    overall = all(r["grounded"] for r in results)
    ungrounded_count = sum(1 for r in results if not r["grounded"])
    if overall:
        note = "All observed_evidence items are grounded in the case's show-command output."
    else:
        note = (
            f"{ungrounded_count} of {len(results)} observed_evidence item(s) "
            f"could not be matched to the case's show-command output -- "
            f"treat this diagnosis with extra scrutiny in human review."
        )
    return overall, results, note


# ---------------------------------------------------------------------------
# Storage: ai_diagnoses.csv
# ---------------------------------------------------------------------------

def save_diagnosis_to_csv(result: dict, out_csv: str = AI_DIAGNOSES_CSV) -> None:
    """Append one diagnosis result to ai_diagnoses.csv, writing the header
    row first if the file doesn't exist yet."""
    file_exists = os.path.exists(out_csv)
    row = {
        "case_id": result["case_id"],
        "timestamp": result["timestamp"],
        "model": result["model"],
        "root_cause": result["root_cause"],
        "confidence": result["confidence"],
        "osi_layer": result["osi_layer"],
        "observed_evidence": json.dumps(result["observed_evidence"]),
        "inference": result["inference"],
        "next_command": result["next_command"],
        "fix_steps": json.dumps(result["fix_steps"]),
        "human_review_required": result["human_review_required"],
        "evidence_grounded": result["evidence_grounded"],
        "ungrounded_evidence_items": json.dumps(result["ungrounded_evidence_items"]),
        "grounding_note": result["grounding_note"],
        "checker_findings_count": result["checker_findings_count"],
        "source_type": result.get("source_type", SOURCE_TYPE_REAL_AI_RUN),
    }
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def diagnose_case(
    case_id: str,
    model: str | None = None,
    cases_csv: str = CASES_CSV,
    prompt_path: str = PROMPT_PATH,
    include_checker_findings: bool = True,
    save_csv: bool = True,
    out_csv: str = AI_DIAGNOSES_CSV,
) -> dict:
    """Run the full Phase 2 pipeline for one case: load case -> build
    prompt -> call model -> parse+validate JSON -> check evidence
    grounding -> (optionally) save to CSV. Returns the full result dict.

    Raises CaseNotFoundError, MissingAPIKeyError, APICallError,
    MalformedJSONError, or ValidationError -- callers (CLI, tests) decide
    how to present each.
    """
    case = load_case(case_id, cases_csv)
    system_prompt = load_system_prompt(prompt_path)
    findings = get_checker_findings(case) if include_checker_findings else []
    user_message = build_user_message(case, findings)

    raw_text = call_model(system_prompt, user_message, model=model)
    data = parse_ai_response(raw_text)
    normalized = validate_diagnosis_schema(data)

    grounded, item_results, grounding_note = check_evidence_grounding(
        normalized["observed_evidence"], case.get("show_output", "")
    )

    result = {
        "case_id": case_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model or DEFAULT_MODEL,
        "root_cause": normalized["root_cause"],
        "confidence": normalized["confidence"],
        "osi_layer": normalized["osi_layer"],
        "observed_evidence": normalized["observed_evidence"],
        "inference": normalized["inference"],
        "next_command": normalized["next_command"],
        "fix_steps": normalized["fix_steps"],
        "human_review_required": normalized["human_review_required"],
        "evidence_grounded": grounded,
        "ungrounded_evidence_items": [r["evidence"] for r in item_results if not r["grounded"]],
        "grounding_note": grounding_note,
        "checker_findings_count": len(findings),
        "raw_response": raw_text,
        "source_type": SOURCE_TYPE_REAL_AI_RUN,
    }

    if save_csv:
        save_diagnosis_to_csv(result, out_csv)

    return result


@dataclass
class BatchSummary:
    succeeded: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def diagnose_all_cases(
    model: str | None = None,
    cases_csv: str = CASES_CSV,
    out_csv: str = AI_DIAGNOSES_CSV,
    save_csv: bool = True,
    on_progress=None,
) -> BatchSummary:
    """Diagnose every case in cases.csv. Stops immediately if the API key
    is missing (it will fail identically for every case), but otherwise
    keeps going after a per-case failure so one bad case doesn't block the
    rest of the batch."""
    get_api_key()  # fail fast, once, with a clear message

    summary = BatchSummary()
    for case_id in list_case_ids(cases_csv):
        try:
            diagnose_case(
                case_id, model=model, cases_csv=cases_csv,
                out_csv=out_csv, save_csv=save_csv,
            )
            summary.succeeded.append(case_id)
            if on_progress:
                on_progress(case_id, None)
        except DiagnosisError as e:
            summary.failed.append({"case_id": case_id, "error": str(e)})
            if on_progress:
                on_progress(case_id, e)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result: dict) -> None:
    print(f"\n=== Diagnosis for {result['case_id']} ===")
    print(f"Model:               {result['model']}")
    print(f"Root cause:          {result['root_cause']}")
    print(f"Confidence:          {result['confidence']}/100")
    print(f"OSI layer:           {result['osi_layer']}")
    print(f"Evidence grounded:   {result['evidence_grounded']}  ({result['grounding_note']})")
    print("Observed evidence:")
    for item in result["observed_evidence"]:
        print(f"  - {item}")
    if result["ungrounded_evidence_items"]:
        print("  ! UNGROUNDED (flagged, not silently accepted):")
        for item in result["ungrounded_evidence_items"]:
            print(f"    - {item}")
    print(f"Inference:           {result['inference']}")
    print(f"Next command:        {result['next_command'] or '(none)'}")
    print("Fix steps:")
    for i, step in enumerate(result["fix_steps"], start=1):
        print(f"  {i}. {step}")
    print(f"Human review required: {result['human_review_required']} (always true by design)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NetSage AI Phase 2 -- AI diagnosis layer (requires ANTHROPIC_API_KEY)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case-id", help="Diagnose a single case, e.g. C001")
    group.add_argument("--all", action="store_true", help="Diagnose every case in cases.csv")
    group.add_argument("--list", action="store_true", help="List valid case IDs and exit")
    parser.add_argument("--model", default=None, help=f"Override model (default: {DEFAULT_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="Don't write to ai_diagnoses.csv")
    parser.add_argument("--no-checker", action="store_true", help="Don't attach rule-checker findings to the prompt")
    parser.add_argument("--out", default=AI_DIAGNOSES_CSV, help="Output CSV path")
    args = parser.parse_args()

    if args.list:
        try:
            for cid in list_case_ids():
                print(cid)
        except DiagnosisError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0

    # Fail fast and clearly if there's no API key, before doing any work.
    try:
        get_api_key()
    except MissingAPIKeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.case_id:
        try:
            result = diagnose_case(
                args.case_id,
                model=args.model,
                include_checker_findings=not args.no_checker,
                save_csv=not args.no_save,
                out_csv=args.out,
            )
        except CaseNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        except MalformedJSONError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            print("--- raw AI response (first 500 chars) ---", file=sys.stderr)
            print(e.raw_text[:500], file=sys.stderr)
            return 1
        except (APICallError, ValidationError, DiagnosisError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        _print_result(result)
        if not args.no_save:
            print(f"\nSaved to {args.out}")
        return 0

    if args.all:
        def progress(case_id, error):
            status = "OK" if error is None else f"FAILED ({error})"
            print(f"[{case_id}] {status}")

        summary = diagnose_all_cases(
            model=args.model, out_csv=args.out,
            save_csv=not args.no_save, on_progress=progress,
        )
        print(f"\nDone. {len(summary.succeeded)} succeeded, {len(summary.failed)} failed.")
        if summary.failed:
            print("Failures:")
            for f in summary.failed:
                print(f"  - {f['case_id']}: {f['error']}")
        if not args.no_save:
            print(f"Results saved to {args.out}")
        return 0 if not summary.failed else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
