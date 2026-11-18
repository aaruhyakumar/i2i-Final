import json
import glob
from pathlib import Path

runs = sorted(glob.glob("test_results/stage2_benchmark/runs/*.json"))
total_cost_rs = 0.0
total_tokens_in = 0
total_tokens_out = 0

print("================================================================================")
print("STAGE 2 BENCHMARK DETAILED CLINICAL & TELEMETRY AUDIT")
print("================================================================================")

for r in runs:
    d = json.load(open(r, encoding="utf-8"))
    meta = d["metadata"]
    telem = d["telemetry"]
    clin = d["clinical_summary"]
    vit = clin["vitals_and_growth"]
    vis = clin["vision_and_atr"]
    data = d["merged_encounter"]["data"]
    fields = d["merged_encounter"]["fields"]

    total_cost_rs += telem.get("cost_rs", 0.0)
    total_tokens_in += telem.get("tokens_in", 0)
    total_tokens_out += telem.get("tokens_out", 0)

    print(f"\n--- {meta['case_id'].upper()} | Config {meta['config_id']} ({meta['config_name']}) ---")
    print(f"  Telemetry: {telem['latency_seconds']}s | In/Out Tokens: {telem['tokens_in']}/{telem['tokens_out']} | Cost: Rs {telem['cost_rs']} ($ {telem.get('cost_usd')})")
    print(f"  Demographics: Name='{data.get('person_name')}', Class='{data.get('class_and_section')}', DOB='{data.get('dob')}', Gender='{data.get('gender')}', Mobile='{data.get('mobile_number')}'")
    print(f"  Vitals: Height='{vit['height']['raw']}' -> norm: '{vit['height']['normalized']}', Weight='{vit['weight']['raw']}' -> norm: '{vit['weight']['normalized']}', Waist='{vit['waist_circumference']['raw']}'")
    print(f"  Growth/Screening: Calculated BMI={vit.get('calculated_bmi')} ({vit.get('bmi_classification')}, Centile: {vit.get('bmi_centile')})")
    print(f"  Vision: R='{vis['vision_distant_right']['raw']}', L='{vis['vision_distant_left']['raw']}', Aid='{vis['vision_aid_status']['raw']}'")
    print(f"  ATR: raw='{vis['spinal_caliper_atr']['raw']}' -> norm: '{vis['spinal_caliper_atr']['normalized']}' (Eval: {vis.get('atr_evaluated')})")
    print(f"  Ears (Otoscope): R='{data.get('otoscope_right_ear')}', L='{data.get('otoscope_left_ear')}'")
    print(f"  Systemic: Nose='{data.get('sys_nose')}', Throat='{data.get('sys_throat')}', Skin='{data.get('sys_skin')}', Hair='{data.get('sys_hair')}'")
    print(f"  Missing fields ({clin['missing_count']}): {d['merged_encounter']['missing_fields']}")
    print(f"  Uncertain fields ({clin['uncertain_count']}): {[u['field'] for u in clin['uncertain_fields']]}")
    print(f"  Conflicts ({clin['conflicts_count']}): {d['merged_encounter']['conflicts']}")
    print(f"  Paediatric Alerts ({len(clin['paediatric_alerts'])}): {clin['paediatric_alerts']}")

print("\n================================================================================")
print(f"TOTAL BENCHMARK EXPENDITURE (10 TRIALS):")
print(f"  Total In Tokens:  {total_tokens_in}")
print(f"  Total Out Tokens: {total_tokens_out}")
print(f"  Total Cost INR:   Rs {round(total_cost_rs, 4)}")
print(f"  Total Cost USD:   $ {round(total_cost_rs / 97.0, 4)}")
print("================================================================================")
