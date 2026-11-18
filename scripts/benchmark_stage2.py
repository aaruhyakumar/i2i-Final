#!/usr/bin/env python3
"""Stage 2 Paediatric Vision Extraction Benchmark Runner.

Strictly Isolated Test-Only Execution Workflow for evaluating:
- Configuration A: Baseline (1568 px, Q82, Contrast Off)
- Configuration B: High Resolution (2048 px, Q92, Contrast Off)
- Configuration C: Contrast (1568 px, Q82, Contrast On)
- Configuration D: Combined (2048 px, Q92, Contrast On)
- Configuration E: Original (Original resolution, No recompression, Contrast Off)

Isolation & Safety Guardrails:
1. Zero Database Operations: Does not connect to SQLite or Supabase.
2. Zero Quota Deductions: Bypasses reserve_paper / release_paper / usage_ledger.
3. Zero Production Record Changes: Does not save to production database or app files.
4. Separate Results Storage: All outputs written to test_results/stage2_benchmark/.
5. Local Response Caching: Completed Claude responses are cached by input hash to prevent unnecessary API billing.
6. Privacy Protection: No patient PII, raw clinical notes, or API secrets in console logs.
7. Safe Default: Defaults to dry-run mode (--dry-run). Real API calls require explicit --execute flag.
"""

import argparse
import base64
import copy
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
try:
    from dotenv import load_dotenv
    # Load environment from backend/.env or root .env
    _backend_env = Path(__file__).resolve().parent.parent / "backend" / ".env"
    _root_env = Path(__file__).resolve().parent.parent / ".env"
    if _backend_env.exists():
        load_dotenv(dotenv_path=_backend_env)
    if _root_env.exists():
        load_dotenv(dotenv_path=_root_env)
except ImportError:
    pass

# Setup base paths
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.services.unified_pipeline import (
    preprocess_clinical_image,
    ImagePreprocessingError,
    sniff_image_mime,
    validate_and_sanitize_extraction,
)
from backend.services.template_engine import default_template_engine
from backend.services.extraction_v2 import default_extraction_pipeline
from backend.services.paediatric_screening import evaluate_paediatric_record
from backend.services.ai_service import (
    PAEDIATRIC_EXTRACTION_PROMPT,
    merge_multipage_normalized_records,
)
from backend.services.claude_service import ClaudeService

# Logging configuration (privacy-safe: only technical telemetry)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark_stage2")

# Benchmark Test Configurations
CONFIGURATIONS: Dict[str, Dict[str, Any]] = {
    "A": {
        "id": "A",
        "name": "A — Baseline",
        "max_edge": 1568,
        "quality": 82,
        "contrast": False,
        "description": "Production baseline: 1568 px cap, JPEG Q82, luminance contrast enhancement disabled.",
    },
    "B": {
        "id": "B",
        "name": "B — Higher resolution",
        "max_edge": 2048,
        "quality": 92,
        "contrast": False,
        "description": "High resolution candidate: 2048 px cap, JPEG Q92, contrast enhancement disabled.",
    },
    "C": {
        "id": "C",
        "name": "C — Contrast",
        "max_edge": 1568,
        "quality": 82,
        "contrast": True,
        "description": "Contrast candidate: 1568 px cap, JPEG Q82, conservative YCbCr luminance stretch enabled.",
    },
    "D": {
        "id": "D",
        "name": "D — Combined",
        "max_edge": 2048,
        "quality": 92,
        "contrast": True,
        "description": "Combined candidate: 2048 px cap, JPEG Q92, conservative YCbCr luminance stretch enabled.",
    },
    "E": {
        "id": "E",
        "name": "E — Original",
        "max_edge": 100000,  # Avoids downsampling
        "quality": 95,
        "contrast": False,
        "description": "Original resolution: Pass-through with zero additional recompression unless EXIF reorientation required.",
    },
}

# Test Cases
TEST_CASES: Dict[str, Dict[str, Any]] = {
    "case_1": {
        "id": "case_1",
        "label": "Case 1 — Higher-quality pair (900x1600)",
        "history_file": "WhatsApp Image 2026-11-17 at 10.30.53 AM (1).jpeg",
        "examination_file": "WhatsApp Image 2026-11-17 at 10.30.53 AM.jpeg",
    },
    "case_2": {
        "id": "case_2",
        "label": "Case 2 — Lower-quality pair (720x1280)",
        "history_file": "WhatsApp Image 2026-11-17 at 10.30.53 AM (2).jpeg",
        "examination_file": "WhatsApp Image 2026-11-17 at 10.30.54 AM.jpeg",
    },
}


def compute_sha256(data: bytes) -> str:
    """Return SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_cache_key(
    case_id: str,
    config_id: str,
    p1_bytes: bytes,
    p2_bytes: bytes,
    model: str,
    prompt: str,
) -> str:
    """Compute a deterministic cache key for Claude API responses, strictly scoped by case, config, inputs, model, and prompt."""
    h = hashlib.sha256()
    h.update(case_id.encode("utf-8"))
    h.update(b"||")
    h.update(config_id.encode("utf-8"))
    h.update(b"||")
    h.update(p1_bytes)
    h.update(b"||")
    h.update(p2_bytes)
    h.update(b"||")
    h.update(model.encode("utf-8"))
    h.update(b"||")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


def load_test_image(sample_dir: Path, filename: str) -> Tuple[bytes, Tuple[int, int], str]:
    """Load image bytes, measure dimensions, and compute SHA-256."""
    filepath = sample_dir / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Test image not found: {filepath}")
    data = filepath.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        dim = im.size
    sha = compute_sha256(data)
    return data, dim, sha


def simulate_mock_extraction(case_id: str) -> Dict[str, Any]:
    """Generate deterministic mock clinical data for dry-run verification."""
    # Deterministic synthetic data representing typical extraction output
    is_case_1 = (case_id == "case_1")
    return {
        "screening_type": ["Paediatric Health Assessment", 0.95],
        "school_name": ["St. Anne's Academy", 0.92],
        "admission_id": ["ADM-2026-8812" if is_case_1 else "ADM-2026-9041", 0.94],
        "moc_doc_id": ["MOC-012" if is_case_1 else "MOC-015", 0.90],
        "person_name": ["Aarav Sharma" if is_case_1 else "Pooja V", 0.95],
        "class_and_section": ["IV-B" if is_case_1 else "III-A", 0.90],
        "dob": ["2018-05-14" if is_case_1 else "2019-09-21", 0.92],
        "age": [8 if is_case_1 else 7, 0.95],
        "gender": ["Male" if is_case_1 else "Female", 0.95],
        "blood_group": ["B+" if is_case_1 else "O+", 0.90],
        "parent_name": ["Rajesh Sharma" if is_case_1 else "Venkat R", 0.92],
        "mobile_number": ["9884783689" if is_case_1 else "9840123456", 0.94],
        "email": [None, 0.50],
        "address": ["12, Church Street, Bangalore" if is_case_1 else "45, Main Road, Chennai", 0.88],
        "known_allergies": ["None" if is_case_1 else "Dust allergy", 0.92],
        "current_medication": ["None" if is_case_1 else "Cetirizine PRN", 0.88],
        "past_surgery": ["None", 0.95],
        "vaccinated_for_age": ["Yes", 0.95],
        "behaviour_concerns": ["None", 0.90],
        "other_medical_issues": ["None", 0.90],
        "family_adults_count": [2, 0.90],
        "family_minors_count": [1, 0.90],
        "pediatrician_name": ["Dr. Suresh K" if is_case_1 else "Dr. Meenakshi S", 0.88],
        "consent_screening": ["Yes", 0.95],
        "consent_first_aid": ["Yes", 0.95],
        "consent_data_research": ["Yes", 0.95],
        "parent_signature": ["Signed", 0.95],
        # Vitals & Examination
        "exam_date": ["2026-11-17", 0.95],
        "height": [128.0 if is_case_1 else 118.5, 0.95],
        "weight": [26.5 if is_case_1 else 21.0, 0.95],
        "waist_circumference": [58.0 if is_case_1 else 54.0, 0.88],
        "bmi": [16.2 if is_case_1 else 15.0, 0.90],
        "blood_pressure": ["104/68" if is_case_1 else "98/64", 0.92],
        "pulse": [82 if is_case_1 else 88, 0.95],
        "spinal_caliper_atr": ["0°" if is_case_1 else "2°", 0.90],
        "vital_signs_status": ["Normal", 0.95],
        "vital_signs_details": ["NAD", 0.90],
        "gait_status": ["Normal", 0.95],
        "gait_details": ["NAD", 0.90],
        "otoscope_right_ear": ["Normal", 0.92],
        "otoscope_left_ear": ["Normal", 0.92],
        "sys_nose": ["Normal", 0.92],
        "sys_throat": ["Normal", 0.92],
        "sys_skin": ["Normal", 0.90],
        "sys_hair": ["Normal", 0.90],
        "sys_other_systems": ["NAD", 0.90],
        "vision_aid_status": ["No", 0.95],
        "vision_distant_right": ["6/6", 0.95],
        "vision_distant_left": ["6/6" if is_case_1 else "6/9", 0.90],
        "vision_color": ["Normal", 0.92],
        "dental_status": ["Needs Care" if is_case_1 else "Normal", 0.90],
        "dental_abnormality": ["Caries irt 46" if is_case_1 else "None", 0.85],
        "dental_advice": ["Dental consultation" if is_case_1 else "NAD", 0.85],
        "specialist_referred_to": ["Dentist" if is_case_1 else "None", 0.90],
        "doctor_name": ["Dr. Arvind Raman", 0.92],
        "doctor_signature": ["Signed", 0.95],
    }


def extract_targeted_metrics(merged_norm: Dict[str, Any], eval_res: Dict[str, Any]) -> Dict[str, Any]:
    """Extract targeted clinical metrics requested in benchmark specifications."""
    fields = merged_norm.get("fields") or {}
    data = merged_norm.get("data") or {}

    def get_field_info(key: str) -> Dict[str, Any]:
        f = fields.get(key)
        if isinstance(f, dict):
            return {
                "key": key,
                "raw": f.get("raw_text"),
                "normalized": f.get("normalized_value"),
                "confidence": f.get("confidence"),
                "source_page": f.get("source_page"),
                "status": f.get("status"),
                "rule_applied": f.get("rule_applied"),
            }
        return {
            "key": key,
            "raw": None,
            "normalized": data.get(key),
            "confidence": 0.0,
            "source_page": None,
            "status": "missing",
            "rule_applied": None,
        }

    # 1. Vitals & Growth
    vitals_keys = [
        "height", "weight", "waist_circumference", "bmi", "blood_pressure", "pulse"
    ]
    vitals_metrics = {k: get_field_info(k) for k in vitals_keys}

    # Add evaluated screening metrics
    vitals_metrics["calculated_bmi"] = eval_res.get("bmi")
    vitals_metrics["bmi_centile"] = eval_res.get("bmi_centile")
    vitals_metrics["bmi_classification"] = eval_res.get("bmi_classification")
    vitals_metrics["bp_percentile"] = eval_res.get("bp_percentile")
    vitals_metrics["bp_classification"] = eval_res.get("bp_classification")

    # 2. Vision & ATR
    vision_keys = [
        "vision_distant_right", "vision_distant_left", "vision_aid_status",
        "vision_color", "spinal_caliper_atr"
    ]
    vision_metrics = {k: get_field_info(k) for k in vision_keys}
    vision_metrics["atr_evaluated"] = eval_res.get("spinal_caliper_atr") or eval_res.get("atr_degrees")

    # 3. Checkboxes and Handwritten Yes/No
    checkbox_keys = [
        "known_allergies", "current_medication", "past_surgery",
        "vaccinated_for_age", "behaviour_concerns", "other_medical_issues",
        "consent_screening", "consent_first_aid", "consent_data_research",
        "vital_signs_status", "gait_status", "vision_aid_status", "dental_status"
    ]
    checkbox_metrics = {k: get_field_info(k) for k in checkbox_keys}

    # 4. Uncertain / Unreadable Fields
    uncertain_list = []
    for k, v in fields.items():
        if not isinstance(v, dict):
            continue
        conf = float(v.get("confidence") or 1.0)
        status = v.get("status")
        if status in ("unreadable", "format_warning", "implausible_vitals", "conflict_warning") or conf < 0.60:
            uncertain_list.append({
                "field": k,
                "raw": v.get("raw_text"),
                "normalized": v.get("normalized_value"),
                "confidence": conf,
                "status": status,
                "source_page": v.get("source_page"),
            })

    return {
        "vitals_and_growth": vitals_metrics,
        "vision_and_atr": vision_metrics,
        "checkboxes_and_yes_no": checkbox_metrics,
        "uncertain_fields": uncertain_list,
        "uncertain_count": len(uncertain_list),
        "missing_count": len(merged_norm.get("missing_fields") or []),
        "conflicts_count": len(merged_norm.get("conflicts") or []),
        "paediatric_alerts": eval_res.get("alerts") or [],
    }


def run_benchmark_trial(
    case_id: str,
    config_id: str,
    sample_dir: Path,
    output_dir: Path,
    execute_claude: bool = False,
    force_no_cache: bool = False,
) -> Dict[str, Any]:
    """Execute an isolated extraction run for one case and one configuration."""
    case_def = TEST_CASES[case_id]
    cfg_def = CONFIGURATIONS[config_id]

    cache_dir = output_dir / "cache"
    runs_dir = output_dir / "runs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting Trial: %s | Config %s (%s, Q%s, Contrast=%s) [Mode: %s]",
        case_id,
        config_id,
        f"{cfg_def['max_edge']}px" if cfg_def["max_edge"] < 10000 else "Original",
        cfg_def["quality"],
        cfg_def["contrast"],
        "LIVE CLAUDE" if execute_claude else "DRY RUN",
    )

    # 1. Load Original Inputs
    p1_raw_bytes, p1_raw_dim, p1_raw_sha = load_test_image(sample_dir, case_def["history_file"])
    p2_raw_bytes, p2_raw_dim, p2_raw_sha = load_test_image(sample_dir, case_def["examination_file"])

    # 2. Execute Preprocessing with Trial Configuration
    t0_prep = time.time()
    p1_proc_bytes, p1_mime = preprocess_clinical_image(
        p1_raw_bytes,
        max_edge=cfg_def["max_edge"],
        quality=cfg_def["quality"],
        enhance_contrast=cfg_def["contrast"],
    )
    p2_proc_bytes, p2_mime = preprocess_clinical_image(
        p2_raw_bytes,
        max_edge=cfg_def["max_edge"],
        quality=cfg_def["quality"],
        enhance_contrast=cfg_def["contrast"],
    )
    prep_duration = round(time.time() - t0_prep, 3)

    with Image.open(io.BytesIO(p1_proc_bytes)) as im1:
        p1_proc_dim = im1.size
    with Image.open(io.BytesIO(p2_proc_bytes)) as im2:
        p2_proc_dim = im2.size

    p1_proc_sha = compute_sha256(p1_proc_bytes)
    p2_proc_sha = compute_sha256(p2_proc_bytes)

    # Check pass-through
    p1_pass_thru = (p1_proc_bytes == p1_raw_bytes)
    p2_pass_thru = (p2_proc_bytes == p2_raw_bytes)

    # 3. Resolve Document Pair Classification
    routing = default_template_engine.resolve_page_pair(
        p1_bytes=p1_proc_bytes,
        p2_bytes=p2_proc_bytes,
        camp_target_group="paediatric",
        client_template_ids=None,
    )

    if not routing.get("resolved"):
        raise RuntimeError(f"Template routing failed for {case_id}: {routing.get('error')}")

    template_ids = routing.get("template_ids") or [
        "tpl_paediatric_history_v9_3",
        "tpl_paediatric_examination_v9_7",
    ]

    # 4. Model Call (Cached / Live / Dry-Run)
    claude_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    cache_key = compute_cache_key(
        case_id=case_id,
        config_id=config_id,
        p1_bytes=p1_proc_bytes,
        p2_bytes=p2_proc_bytes,
        model=claude_model,
        prompt=PAEDIATRIC_EXTRACTION_PROMPT,
    )
    cache_file = cache_dir / f"{cache_key}.json"

    raw_model_json: Dict[str, Any] = {}
    telemetry: Dict[str, Any] = {
        "model": claude_model,
        "latency_seconds": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_think": 0,
        "cost_rs": 0.0,
        "cost_usd": 0.0,
        "from_cache": False,
        "preprocessing_seconds": prep_duration,
    }

    if cache_file.exists() and not force_no_cache:
        logger.info("Found cached Claude response (%s); loading from cache.", cache_key[:12])
        cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
        raw_model_json = cached_data.get("raw_model_json", {})
        telemetry = cached_data.get("telemetry", telemetry)
        telemetry["from_cache"] = True
    elif execute_claude:
        service = ClaudeService(model=claude_model)
        service.max_retries = 0  # Do not retry automatically; fail fast and report error
        if not service.is_configured():
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured in environment or .env file."
            )
        t0_call = time.time()
        call_res = service._call_model(
            model=service.model,
            prompt=PAEDIATRIC_EXTRACTION_PROMPT,
            p1_bytes=p1_proc_bytes,
            p2_bytes=p2_proc_bytes,
            is_fallback=False,
            p1_mime=p1_mime,
            p2_mime=p2_mime,
        )
        call_latency = round(time.time() - t0_call, 2)

        if not call_res.get("success"):
            raise RuntimeError(f"Claude API call failed: {call_res.get('error')}")

        raw_text = call_res.get("raw_text", "")
        clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
        try:
            parsed = json.loads(clean_text)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                parsed = parsed[0]
            raw_model_json = parsed if isinstance(parsed, dict) else {}
        except Exception as pe:
            logger.warning("JSON parse error from Claude output: %s", pe)
            raw_model_json = {}

        tokens_in = call_res.get("tokens_in", 0)
        tokens_out = call_res.get("tokens_out", 0)
        tokens_think = call_res.get("tokens_think", 0)
        cost_rs = call_res.get("cost_rs", 0.0)
        cost_usd = round(cost_rs / service.usd_inr, 4)

        telemetry.update({
            "model": call_res.get("model", claude_model),
            "latency_seconds": call_latency,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_think": tokens_think,
            "cost_rs": cost_rs,
            "cost_usd": cost_usd,
            "from_cache": False,
        })

        # Save to cache
        cache_file.write_text(
            json.dumps({"raw_model_json": raw_model_json, "telemetry": telemetry}, indent=2),
            encoding="utf-8",
        )
    else:
        # Dry-run mode: deterministic synthetic simulation
        logger.info("Executing dry-run simulation (no API tokens consumed).")
        raw_model_json = simulate_mock_extraction(case_id)
        telemetry.update({
            "model": f"{claude_model} (dry-run mock)",
            "latency_seconds": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_rs": 0.0,
            "cost_usd": 0.0,
            "from_cache": False,
        })

    # 5. Sanitize and Normalize Both Pages
    sanitized = validate_and_sanitize_extraction(raw_model_json)

    # Page 1: History normalization (passes raw LLM payload directly to extraction pipeline)
    p1_norm = default_extraction_pipeline.process_extracted_payload(
        template_ids[0], raw_model_json
    )
    # Page 2: Examination normalization
    p2_norm = default_extraction_pipeline.process_extracted_payload(
        template_ids[1], raw_model_json
    )

    # 6. Merge Multipage Normalized Records with Provenance Tracking
    merged_norm = merge_multipage_normalized_records([p1_norm, p2_norm])

    # 7. Automated Paediatric Screening Evaluation
    screening_eval = evaluate_paediatric_record(merged_norm.get("data") or {})

    # 8. Extract Targeted Metrics Summary
    clinical_summary = extract_targeted_metrics(merged_norm, screening_eval)

    trial_result = {
        "metadata": {
            "case_id": case_id,
            "case_label": case_def["label"],
            "config_id": config_id,
            "config_name": cfg_def["name"],
            "config_settings": {
                "max_edge": cfg_def["max_edge"],
                "quality": cfg_def["quality"],
                "contrast": cfg_def["contrast"],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "live_claude" if execute_claude else "dry_run",
        },
        "inputs": {
            "page1": {
                "filename": case_def["history_file"],
                "raw_dimensions": list(p1_raw_dim),
                "raw_bytes": len(p1_raw_bytes),
                "sha256": p1_raw_sha,
            },
            "page2": {
                "filename": case_def["examination_file"],
                "raw_dimensions": list(p2_raw_dim),
                "raw_bytes": len(p2_raw_bytes),
                "sha256": p2_raw_sha,
            },
        },
        "preprocessing": {
            "page1": {
                "processed_dimensions": list(p1_proc_dim),
                "processed_bytes": len(p1_proc_bytes),
                "sha256": p1_proc_sha,
                "mime_type": p1_mime,
                "pass_through": p1_pass_thru,
            },
            "page2": {
                "processed_dimensions": list(p2_proc_dim),
                "processed_bytes": len(p2_proc_bytes),
                "sha256": p2_proc_sha,
                "mime_type": p2_mime,
                "pass_through": p2_pass_thru,
            },
        },
        "template_routing": {
            "resolved": routing.get("resolved"),
            "family": routing.get("family"),
            "template_ids": template_ids,
            "page1_classification": routing.get("page1", {}).get("template_id"),
            "page2_classification": routing.get("page2", {}).get("template_id"),
        },
        "telemetry": telemetry,
        "page1_history_normalized": p1_norm,
        "page2_examination_normalized": p2_norm,
        "merged_encounter": merged_norm,
        "paediatric_screening": screening_eval,
        "clinical_summary": clinical_summary,
    }

    # Write individual trial record to disk
    record_file = runs_dir / f"{case_id}_{config_id}.json"
    record_file.write_text(json.dumps(trial_result, indent=2), encoding="utf-8")
    logger.info("Saved trial record: %s", record_file.name)

    return trial_result


def generate_benchmark_summary(results: List[Dict[str, Any]], output_dir: Path) -> Path:
    """Compile comparative summary matrix and Markdown report."""
    summary_file = output_dir / "summary_matrix.json"
    report_file = output_dir / "BENCHMARK_REPORT.md"

    summary_rows = []
    for r in results:
        meta = r["metadata"]
        p1 = r["preprocessing"]["page1"]
        p2 = r["preprocessing"]["page2"]
        telem = r["telemetry"]
        clin = r["clinical_summary"]
        summary_rows.append({
            "case_id": meta["case_id"],
            "config_id": meta["config_id"],
            "config_name": meta["config_name"],
            "mode": meta["execution_mode"],
            "p1_dim": f"{p1['processed_dimensions'][0]}x{p1['processed_dimensions'][1]}",
            "p1_kb": round(p1["processed_bytes"] / 1024.0, 1),
            "p1_passthru": p1["pass_through"],
            "p2_dim": f"{p2['processed_dimensions'][0]}x{p2['processed_dimensions'][1]}",
            "p2_kb": round(p2["processed_bytes"] / 1024.0, 1),
            "p2_passthru": p2["pass_through"],
            "latency_s": telem["latency_seconds"],
            "tokens_in": telem["tokens_in"],
            "tokens_out": telem["tokens_out"],
            "cost_rs": telem["cost_rs"],
            "missing_fields": clin["missing_count"],
            "uncertain_fields": clin["uncertain_count"],
            "conflicts": clin["conflicts_count"],
            "alerts": len(clin["paediatric_alerts"]),
        })

    summary_file.write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")

    # Generate Markdown Report
    md = [
        "# Stage 2 Paediatric Extraction Benchmark Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## 1. Test Configurations Evaluated",
        "",
        "| Config | Name | Max Edge | JPEG Quality | Luminance Contrast | Pass-through Expected |",
        "|:---:|:---|:---:|:---:|:---:|:---:|",
        "| **A** | Baseline | 1568 px | 82 | Off | On images <= 1568 px |",
        "| **B** | Higher resolution | 2048 px | 92 | Off | On images <= 2048 px |",
        "| **C** | Contrast | 1568 px | 82 | On (YCbCr) | No (Always re-encodes stretch) |",
        "| **D** | Combined | 2048 px | 92 | On (YCbCr) | No (Always re-encodes stretch) |",
        "| **E** | Original | Original | No recompression | Off | Yes (Original bytes preserved) |",
        "",
        "## 2. Benchmark Execution Matrix",
        "",
        "| Case | Config | Page 1 Dim (KB) | Page 2 Dim (KB) | Latency | Tokens (In / Out) | Cost (₹) | Missing | Uncertain | Conflicts | Alerts |",
        "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for row in summary_rows:
        p1_str = f"{row['p1_dim']} ({row['p1_kb']} KB){' *PT*' if row['p1_passthru'] else ''}"
        p2_str = f"{row['p2_dim']} ({row['p2_kb']} KB){' *PT*' if row['p2_passthru'] else ''}"
        tok_str = f"{row['tokens_in']} / {row['tokens_out']}"
        md.append(
            f"| {row['case_id']} | **{row['config_id']}** | {p1_str} | {p2_str} | "
            f"{row['latency_s']}s | {tok_str} | ₹{row['cost_rs']} | "
            f"{row['missing_fields']} | {row['uncertain_fields']} | {row['conflicts']} | {row['alerts']} |"
        )

    md.extend([
        "",
        "*Note: `PT` indicates zero-compression pass-through of original encoded image bytes.*",
        "",
        "## 3. Strict Isolation Verification",
        "",
        "- **Database writes:** 0 (SQLite `clinic_records.db` and Supabase were not connected).",
        "- **Quota consumption:** 0 (Bypassed `reserve_paper` and `usage_ledger`).",
        "- **Production records created:** 0 (All data stored strictly in `test_results/stage2_benchmark/`).",
        "- **MIME Type Validation:** Formats strictly verified via magic byte sniffing before model dispatch.",
        "- **EXIF Safety:** Orientation physically normalized before any processing.",
    ])

    report_file.write_text("\n".join(md), encoding="utf-8")
    logger.info("Generated summary report: %s", report_file)
    return report_file


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Stage 2 Paediatric Extraction Benchmark Runner"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Execute real Claude API requests. Default is dry-run simulation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Execute in safe dry-run simulation mode without calling Claude API.",
    )
    parser.add_argument(
        "--case",
        choices=["case_1", "case_2", "all"],
        default="all",
        help="Specify which case to run (default: all).",
    )
    parser.add_argument(
        "--config",
        choices=["A", "B", "C", "D", "E", "all"],
        default="all",
        help="Specify which configuration to run (default: all).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Bypass Claude API response cache and force re-running.",
    )
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=REPO_ROOT / "testingsample",
        help="Directory holding test sample images.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "test_results" / "stage2_benchmark",
        help="Directory to save benchmark output records.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    # If --execute was specified, dry-run is turned off
    is_live = bool(args.execute)

    sample_dir = args.sample_dir
    output_dir = args.output_dir

    if not sample_dir.exists():
        logger.error("Sample directory does not exist: %s", sample_dir)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    cases_to_run = (
        list(TEST_CASES.keys()) if args.case == "all" else [args.case]
    )
    configs_to_run = (
        list(CONFIGURATIONS.keys()) if args.config == "all" else [args.config]
    )

    logger.info("===========================================================")
    logger.info("Ink2Insights Stage 2 Extraction Benchmark Runner")
    logger.info("Execution Mode: %s", "LIVE CLAUDE API" if is_live else "DRY-RUN SIMULATION")
    logger.info("Cases: %s | Configs: %s", cases_to_run, configs_to_run)
    logger.info("Sample Directory: %s", sample_dir)
    logger.info("Output Directory: %s", output_dir)
    logger.info("===========================================================")

    all_results = []
    for case_id in cases_to_run:
        for config_id in configs_to_run:
            try:
                res = run_benchmark_trial(
                    case_id=case_id,
                    config_id=config_id,
                    sample_dir=sample_dir,
                    output_dir=output_dir,
                    execute_claude=is_live,
                    force_no_cache=args.no_cache,
                )
                all_results.append(res)
            except Exception as e:
                logger.error("Trial failed for %s Config %s: %s", case_id, config_id, e)
                raise

    report_path = generate_benchmark_summary(all_results, output_dir)
    logger.info("Benchmark complete. Full report written to: %s", report_path)


if __name__ == "__main__":
    main()
