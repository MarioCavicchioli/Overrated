"""Answers the VOA advocate's custom tool calls. Runs in the orchestrator process.

One AdvocateHandlers per advocate session, so per pack. The data tools are the
builder's (run_data_tool); submit_verdict is the advocate's only write, and code
checks every cited uarn before the verdicts row is written:
- it exists in the data service, and
- the advocate read it through a data tool in this session.
"""

from __future__ import annotations

from typing import Any

from agents.builder.handlers import DATA_TOOLS, _short, run_data_tool
from orchestrator.data_client import DataClient, NotFound
from orchestrator.store import Store

AGENT = "advocate"
VERSION = "advocate-v2"
MAX_SUBMIT_ATTEMPTS = 3
VERDICTS = ("survive", "kill")
KINDS = ("cherry_picking", "wrong_exclusion", "different_use", "different_size",
         "location", "weak_comparable", "maths_error", "overstated", "sound")
MAX_REASONS = 8

TOOLS = DATA_TOOLS + ("submit_verdict",)


class AdvocateHandlers:
    def __init__(self, store: Store, data: DataClient, case_id: str):
        self.store = store
        self.data = data
        self.case_id = case_id
        self.seen: set[str] = set()   # uarns returned by a data tool this session
        self.attempts = 0
        self.verdict: dict[str, Any] | None = None   # set once accepted

    def handles(self, name: str) -> bool:
        return name in TOOLS

    def handle(self, name: str, args: dict[str, Any]) -> str:
        if name in DATA_TOOLS:
            return self._data_tool(name, args)
        if name == "submit_verdict":
            return self._submit_verdict(args)
        return f"ERROR: unknown tool {name}"

    # -- data service ----------------------------------------------------
    def _data_tool(self, name: str, args: dict[str, Any]) -> str:
        text, result = run_data_tool(self.data, name, args)
        if result is not None:
            rows = result if isinstance(result, list) else [result]
            self.seen.update(r["uarn"] for r in rows if isinstance(r, dict) and "uarn" in r)
            self.store.add_event(AGENT, "tool", f"{name} {_short(args)} -> {len(rows)} result(s)",
                                 case_id=self.case_id)
        return text

    # -- submit_verdict --------------------------------------------------
    def check_verdict(self, args: dict[str, Any]) -> list[str]:
        """Every problem with a submitted verdict; empty means it can be written."""
        errors = []
        if args.get("verdict") not in VERDICTS:
            errors.append(f"verdict must be one of {', '.join(VERDICTS)}")
        reasons = args.get("reasons")
        if not isinstance(reasons, list) or not 1 <= len(reasons) <= MAX_REASONS:
            return errors + [f"reasons must be a list of 1 to {MAX_REASONS} items"]
        for i, r in enumerate(reasons):
            tag = f"reasons[{i}]"
            if not isinstance(r, dict):
                errors.append(f"{tag}: must be an object")
                continue
            if r.get("kind") not in KINDS:
                errors.append(f"{tag}: kind must be one of {', '.join(KINDS)}")
            if len(str(r.get("reason", "")).strip()) < 20:
                errors.append(f"{tag}: reason must be at least 20 characters")
            uarn = str(r.get("voa_record", "")).strip()
            if not uarn:
                errors.append(f"{tag}: voa_record (a uarn) is required")
                continue
            try:
                self.data.get_valuation(uarn)
            except NotFound:
                errors.append(f"{tag}: voa_record {uarn} does not exist in the data service")
                continue
            if uarn not in self.seen:
                errors.append(f"{tag}: voa_record {uarn} was not read through a data tool in "
                              "this session; look it up before citing it")
        return errors

    def _submit_verdict(self, args: dict[str, Any]) -> str:
        if self.verdict is not None:
            return "ALREADY RECORDED. Your verdict for this pack is saved; your work is done."
        self.attempts += 1
        if self.attempts > MAX_SUBMIT_ATTEMPTS:
            return f"REJECTED: no attempts left ({MAX_SUBMIT_ATTEMPTS} used). Stop here."
        errors = self.check_verdict(args)
        if errors:
            self.store.add_event(AGENT, "verdict_rejected",
                                 f"attempt {self.attempts}, {len(errors)} problem(s): {errors[0]}",
                                 case_id=self.case_id)
            left = MAX_SUBMIT_ATTEMPTS - self.attempts
            return ("REJECTED. Nothing was written. Fix every problem and call submit_verdict "
                    f"again ({left} attempt(s) left):\n- " + "\n- ".join(errors))

        # verdicts.reasons contract: [{reason, voa_record, kind}]
        reasons = [{"reason": r["reason"].strip(), "voa_record": r["voa_record"].strip(),
                    "kind": r["kind"]} for r in args["reasons"]]
        self.store.insert_verdict({
            "case_id": self.case_id, "verdict": args["verdict"], "version": VERSION,
            "reasons": reasons,
        })
        self.verdict = {"verdict": args["verdict"], "reasons": reasons}
        kinds = sorted({r["kind"] for r in reasons})
        self.store.add_event(
            AGENT, "verdict",
            f"{args['verdict']}: {len(reasons)} reason(s) ({', '.join(kinds)}), citing "
            + ", ".join(sorted({r['voa_record'] for r in reasons})),
            case_id=self.case_id)
        return f"ACCEPTED. Verdict '{args['verdict']}' recorded. Your work on this pack is done."
