"""Answers the Case builder's custom tool calls. Runs in the orchestrator process.

The builder runs inside the Scout's workflow runs, so its calls arrive on the
Scout's session stream; whatever loop reads that stream passes them here:

    if event.type == "agent.custom_tool_use" and builder_handlers.handles(event.name):
        text = builder_handlers.handle(event.name, event.input)

Data service results are wrapped in <data_service_result> tags: they are data,
and the system prompt tells the builder never to follow instructions inside them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from agents.builder import rates
from agents.builder.checks import check_pack, eligible_comparables
from agents.builder.pdf import render_pack_pdf
from orchestrator.data_client import DataClient, NotFound
from orchestrator.store import Store

AGENT = "builder"
VERSION = "builder-v2"   # v2: proposed value keeps the value outside the zones; core-use check
MAX_SUBMIT_ATTEMPTS = 3

DATA_TOOLS = ("search_properties", "get_valuation", "comparables", "street_tone")
TOOLS = DATA_TOOLS + ("eligible_comparables", "submit_pack", "no_case")


class BuilderHandlers:
    def __init__(self, store: Store, data: DataClient, run_id: str | None = None,
                 revise_ref: str | None = None):
        """run_id: the Scout run whose candidates this builder works on, when it runs in
        the Scout's workflow runs (a UARN can be a candidate in several runs).
        revise_ref: rebuild that case's pack as a new version under the same ref."""
        self.store = store
        self.data = data
        self.run_id = run_id
        self.revise_ref = revise_ref
        self.attempts: dict[str, int] = defaultdict(int)   # uarn -> submit_pack calls

    def handles(self, name: str) -> bool:
        return name in TOOLS

    def handle(self, name: str, args: dict[str, Any]) -> str:
        if name in DATA_TOOLS:
            return self._data_tool(name, args)
        if name == "eligible_comparables":
            return self._eligible(args)
        if name == "submit_pack":
            return self._submit_pack(args)
        if name == "no_case":
            return self._no_case(args)
        return f"ERROR: unknown tool {name}"

    # -- data service ----------------------------------------------------
    def _data_tool(self, name: str, args: dict[str, Any]) -> str:
        text, result = run_data_tool(self.data, name, args)
        if result is not None:
            size = len(result) if isinstance(result, list) else 1
            self.store.add_event(AGENT, "tool", f"{name} {_short(args)} -> {size} result(s)")
        return text

    def _eligible(self, args: dict[str, Any]) -> str:
        try:
            result = eligible_comparables(args.get("uarn", ""), self.data)
        except NotFound as e:
            return f"ERROR: {e}"
        self.store.add_event(AGENT, "tool",
                             f"eligible_comparables uarn={args.get('uarn')} -> {len(result)} result(s)")
        return ('<data_service_result tool="eligible_comparables">\n'
                f"{json.dumps(result, indent=1)}\n</data_service_result>")

    # -- submit_pack -----------------------------------------------------
    def _submit_pack(self, args: dict[str, Any]) -> str:
        uarn = args.get("uarn", "?")
        self.attempts[uarn] += 1
        attempt = self.attempts[uarn]
        if attempt > MAX_SUBMIT_ATTEMPTS:
            return (f"REJECTED: no attempts left ({MAX_SUBMIT_ATTEMPTS} used). "
                    "Call no_case with the reason the pack could not be fixed.")

        # case_id, version and annual_saving are set by code, never by the model;
        # a placeholder case_id lets the schema check run.
        pack = {k: v for k, v in args.items() if k != "annual_saving"}
        pack.update(case_id="c_0000", version=VERSION,
                    annual_saving=self._saving(uarn, pack.get("proposed_rv")))
        errors = check_pack(pack, self.data)
        if errors:
            self.store.add_event(AGENT, "pack_rejected",
                                 f"{uarn}: attempt {attempt}, {len(errors)} problem(s): "
                                 f"{errors[0]}", run_id=self.run_id)
            left = MAX_SUBMIT_ATTEMPTS - attempt
            return ("REJECTED. Nothing was written. Fix every problem and call submit_pack "
                    f"again ({left} attempt(s) left), or call no_case if it can't be fixed:\n- "
                    + "\n- ".join(errors))

        if self.revise_ref:
            old = self.store.get_pack(self.revise_ref)
            if old is None or old["uarn"] != uarn:
                return f"ERROR: {self.revise_ref} is not a pack for {uarn}"
            pack["case_id"] = self.revise_ref
            version = int(old.get("pack_version") or 1) + 1
            pdf_name = f"{self.revise_ref}_v{version}"
        else:
            pack["case_id"] = self.store.next_case_id()
            version, pdf_name = 1, pack["case_id"]
        pdf_url = self.store.save_pdf(pdf_name, render_pack_pdf(pack))
        row = {
            "case_id": pack["case_id"], "uarn": uarn, "pack": pack,
            "comparables": pack["comparables"], "proposed_rv": pack["proposed_rv"],
            "annual_saving": pack["annual_saving"], "argument": pack["argument"],
            "pdf_url": pdf_url, "version": VERSION, "run_id": self.run_id,
        }
        if self.revise_ref:
            self.store.insert_pack_version(row)
        else:
            self.store.insert_pack(row)
        self.store.set_candidate_status(uarn, "pack_ready", self.run_id)
        self.store.add_event(
            AGENT, "pack_written",
            f"{pack['address']}: £{pack['current_rv']:,} -> £{pack['proposed_rv']:,}, "
            f"saving £{pack['annual_saving']:,}/yr, {len(pack['comparables'])} comparables"
            + (f" (version {version})" if version > 1 else ""),
            case_id=pack["case_id"], run_id=self.run_id)
        return (f"ACCEPTED. Pack {pack['case_id']} written, annual saving "
                f"£{pack['annual_saving']:,}. Your work on this candidate is done.")

    def _saving(self, uarn: str, proposed_rv: Any) -> int:
        """Bill difference from the data service's current value. 0 when it can't be
        computed; check_pack then reports the real problem (unknown uarn, bad proposed_rv)."""
        if not isinstance(proposed_rv, int):
            return 0
        try:
            subject = self.data.get_valuation(uarn)
        except NotFound:
            return 0
        return max(0, rates.annual_saving(subject["rv"], proposed_rv, rates.use_of(subject)))

    # -- no_case ---------------------------------------------------------
    def _no_case(self, args: dict[str, Any]) -> str:
        uarn, reason = args.get("uarn", ""), args.get("reason", "").strip()
        if not reason:
            return "ERROR: give a reason"
        if self.store.get_candidate(uarn, self.run_id) is None:
            return f"ERROR: {uarn} is not in candidates" + (
                f" for run {self.run_id}" if self.run_id else "")
        self.store.set_candidate_status(uarn, "no_case", self.run_id)
        self.store.add_event(AGENT, "no_case", f"{uarn}: {reason}", run_id=self.run_id)
        return "Recorded: dropped by builder. Your work on this candidate is done."


def run_data_tool(data: DataClient, name: str, args: dict[str, Any]) -> tuple[str, Any]:
    """Call one of the four read-only data tools. Returns the text for the model,
    wrapped in <data_service_result> tags, and the raw result (None on error).
    Shared by every agent that has the data tools."""
    if name not in DATA_TOOLS:
        return f"ERROR: unknown tool {name}", None
    try:
        result = getattr(data, name)(**args)
    except NotFound as e:
        return f"ERROR: {e}", None
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}", None
    return (f'<data_service_result tool="{name}">\n'
            f"{json.dumps(result, indent=1)}\n</data_service_result>"), result


def _short(args: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in args.items())
