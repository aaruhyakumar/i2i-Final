import json

configs = ['A', 'B', 'C', 'D', 'E']

def audit_case(case_id):
    print(f"================================================================================")
    print(f"GROUND TRUTH & CONFIG VARIATION AUDIT: {case_id.upper()}")
    print(f"================================================================================")
    
    trials = {}
    for cfg in configs:
        with open(f"test_results/stage2_benchmark/runs/{case_id}_{cfg}.json", encoding="utf-8") as f:
            trials[cfg] = json.load(f)
            
    # Check demographics
    fields = [
        "screening_type", "school_name", "admission_id", "moc_doc_id",
        "person_name", "class_and_section", "dob", "age", "gender", "blood_group",
        "parent_name", "mobile_number", "email", "address",
        "known_allergies", "current_medication", "past_surgery", "vaccinated_for_age",
        "behaviour_concerns", "other_medical_issues", "family_adults_count", "family_minors_count",
        "pediatrician_name", "consent_screening", "consent_first_aid", "consent_data_research",
        "parent_signature", "exam_date", "height", "weight", "waist_circumference",
        "bmi", "blood_pressure", "pulse", "spinal_caliper_atr",
        "vital_signs_status", "vital_signs_details", "gait_status", "gait_details",
        "otoscope_right_ear", "otoscope_left_ear", "sys_nose", "sys_throat",
        "sys_skin", "sys_hair", "sys_other_systems", "vision_aid_status",
        "vision_distant_right", "vision_distant_left", "vision_color",
        "dental_status", "dental_abnormality", "dental_advice", "specialist_referred_to",
        "doctor_name", "doctor_signature"
    ]
    
    for f in fields:
        raw_vals = {}
        norm_vals = {}
        confs = {}
        for cfg in configs:
            f_data = trials[cfg]["merged_encounter"]["fields"].get(f, {})
            raw_vals[cfg] = f_data.get("raw_text")
            norm_vals[cfg] = f_data.get("normalized_value")
            confs[cfg] = f_data.get("confidence")
            
        unique_raw = set(raw_vals.values())
        unique_norm = set(norm_vals.values())
        
        is_diff = len(unique_raw) > 1 or len(unique_norm) > 1
        tag = " [VARIATION]" if is_diff else " [STABLE]"
        print(f"\nField: {f}{tag}")
        for cfg in configs:
            print(f"  Config {cfg}: raw='{raw_vals[cfg]}' | norm='{norm_vals[cfg]}' | conf={confs[cfg]}")

audit_case("case_1")
audit_case("case_2")
