"""Unit and isolation tests for Stage 2 Paediatric Vision Extraction Benchmark Runner."""

import ast
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_SCRIPT = REPO_ROOT / "scripts" / "benchmark_stage2.py"
DB_PATH = REPO_ROOT / "backend" / "clinic_records.db"


def test_benchmark_script_ast_isolation():
    """Verify that scripts/benchmark_stage2.py contains ZERO imports of backend.database."""
    assert BENCHMARK_SCRIPT.exists(), f"Benchmark runner not found at {BENCHMARK_SCRIPT}"
    source = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_imports = {"database", "backend.database", "backend.app", "app"}
    found_forbidden = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_imports:
                    found_forbidden.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in forbidden_imports or any(mod.startswith(f"{fi}.") for fi in forbidden_imports):
                found_forbidden.append(mod)

    assert not found_forbidden, f"Benchmark script imports forbidden persistence modules: {found_forbidden}"


def test_benchmark_script_has_zero_quota_calls():
    """Verify that scripts/benchmark_stage2.py does not invoke reserve_paper or release_paper."""
    source = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_calls = {"reserve_paper", "release_paper", "save_private_file", "save_scan_hash", "create_record"}
    found_calls = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # function name can be an Identifier or an Attribute (e.g. database.reserve_paper)
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name in forbidden_calls:
                found_calls.append(func_name)

    assert not found_calls, f"Benchmark script contains forbidden function calls: {found_calls}"


def test_dry_run_execution_isolation(tmp_path):
    """Run benchmark trial in dry-run mode and verify DB mtime and contents remain untouched."""
    from scripts.benchmark_stage2 import (
        run_benchmark_trial,
        generate_benchmark_summary,
        CONFIGURATIONS,
        TEST_CASES,
    )

    db_mtime_before = DB_PATH.stat().st_mtime if DB_PATH.exists() else None

    # Get paper counts before
    paper_count_before = None
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT COUNT(*) FROM records;")
            record_count_before = c.fetchone()[0]
        except Exception:
            record_count_before = None
        conn.close()

    # Execute Case 1 with Config A and Config E
    sample_dir = REPO_ROOT / "testingsample"
    trial_a = run_benchmark_trial(
        case_id="case_1",
        config_id="A",
        sample_dir=sample_dir,
        output_dir=tmp_path,
        execute_claude=False,
    )
    trial_e = run_benchmark_trial(
        case_id="case_1",
        config_id="E",
        sample_dir=sample_dir,
        output_dir=tmp_path,
        execute_claude=False,
    )

    # Verify Configuration A downsampled
    p1_a = trial_a["preprocessing"]["page1"]
    assert p1_a["processed_dimensions"] == [882, 1568]
    assert p1_a["pass_through"] is False

    # Verify Configuration E preserved original bytes and dimensions
    p1_e = trial_e["preprocessing"]["page1"]
    assert p1_e["processed_dimensions"] == [900, 1600]
    assert p1_e["pass_through"] is True
    assert p1_e["processed_bytes"] == trial_e["inputs"]["page1"]["raw_bytes"]

    # Verify DB mtime did not change
    if DB_PATH.exists() and db_mtime_before is not None:
        db_mtime_after = DB_PATH.stat().st_mtime
        assert db_mtime_before == db_mtime_after, "Database file modified during benchmark dry run!"

        # Verify record count did not change
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM records;")
        assert c.fetchone()[0] == record_count_before
        conn.close()


def test_cache_key_isolation_across_configs():
    """Verify that identical image bytes under different configuration IDs produce unique cache keys."""
    from scripts.benchmark_stage2 import compute_cache_key

    fake_p1 = b"\xff\xd8\xff\xe0fake_jpeg_history_data"
    fake_p2 = b"\xff\xd8\xff\xe0fake_jpeg_examination_data"
    model = "claude-sonnet-5-5"
    prompt = "Test extraction prompt"

    # 1. Verify different config IDs produce unique keys for identical image bytes
    configs = ["A", "B", "C", "D", "E"]
    keys = {
        cfg: compute_cache_key(
            case_id="case_2",
            config_id=cfg,
            p1_bytes=fake_p1,
            p2_bytes=fake_p2,
            model=model,
            prompt=prompt,
        )
        for cfg in configs
    }
    assert len(set(keys.values())) == len(configs), "Collision detected in cache keys across different configurations!"

    # 2. Verify case_id differentiates cache keys
    key_case1_a = compute_cache_key("case_1", "A", fake_p1, fake_p2, model, prompt)
    key_case2_a = compute_cache_key("case_2", "A", fake_p1, fake_p2, model, prompt)
    assert key_case1_a != key_case2_a, "Case ID must differentiate cache keys"

    # 3. Verify page order differentiates cache keys
    key_inverted = compute_cache_key("case_1", "A", fake_p2, fake_p1, model, prompt)
    assert key_case1_a != key_inverted, "Page order must differentiate cache keys"

