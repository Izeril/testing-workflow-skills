#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public business-impact translator with explicit two-stage mode labels."""

from datetime import datetime
from business_impact_gate import business_impact_execution, completed_code_target

from business_impact_translator_core import translate_business_impacts as _translate
from business_impact_readability import make_tester_readable


def translate_business_impacts(
    requirement_facts,
    target_results,
    codegraph_impact,
    project_info,
):
    execution = business_impact_execution(target_results)
    analyzed = [target for target in target_results if completed_code_target(target)]
    if not analyzed:
        status = execution["status"]
        return {
            "schema_version": "1.1.0", "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status, "skip_reason": execution["reason"] if status == "skipped" else "",
            "block_reason": execution["reason"] if status == "blocked" else "",
            "execution": execution,
            "analysis_mode": "not_applicable" if status == "skipped" else "code_analysis_blocked",
            "items": [], "evidence_catalog": [],
            "summary": {"total": 0, "confirmed": 0, "high": 0, "medium": 0,
                        "potential": 0, "grouped_objects": 0, "tester_readable": 0},
        }
    payload = _translate(
        requirement_facts,
        analyzed,
        codegraph_impact,
        project_info,
    )
    payload = make_tester_readable(
        payload, requirement_facts, analyzed, project_info
    )
    formal_targets = [
        target
        for target in target_results
        if str(target.get("analysis_mode") or "uat") == "uat"
    ]
    if formal_targets and payload.get("analysis_mode") != "uat_confirmed":
        uat_code_ready = all(
            target.get("effective_analysis_mode") == "uat"
            and (target.get("uat_merge_gate") or {}).get("status") == "fully_merged"
            for target in formal_targets
        )
        if uat_code_ready:
            # The analyzed source really is UAT code. Keep evidence gates
            # visible without incorrectly calling the result development
            # preanalysis.
            source_ready = all(
                target.get("codegraph_status") in {"ready", "source_verified"}
                for target in formal_targets
            ) and any(target.get("codegraph_status") == "source_verified" for target in formal_targets)
            payload["analysis_mode"] = "uat_source_verified" if source_ready else "uat_merged_evidence_incomplete"
        else:
            # Development evidence is retained, but the label prevents
            # consumers from mistaking it for the verified UAT environment.
            payload["analysis_mode"] = "uat_blocked_with_development_preanalysis"
    payload["status"] = execution["status"]
    payload["execution"] = execution
    if execution["status"] == "blocked":
        payload["block_reason"] = execution["reason"]
        payload["analysis_mode"] = "code_analysis_blocked"
    return payload
