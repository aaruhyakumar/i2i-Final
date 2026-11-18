import json

configs = ['A', 'B', 'C', 'D', 'E']
for case in ['case_1', 'case_2']:
    print(f"============================== {case.upper()} ==============================")
    data_by_cfg = {}
    raw_by_cfg = {}
    for cfg in configs:
        fpath = f'test_results/stage2_benchmark/runs/{case}_{cfg}.json'
        d = json.load(open(fpath, encoding='utf-8'))
        data_by_cfg[cfg] = d['merged_encounter']['data']
        # also get raw_text from merged_encounter fields
        raw_by_cfg[cfg] = {k: v.get('raw_text') for k, v in d['merged_encounter']['fields'].items()}
    
    all_keys = sorted(list(set().union(*[d.keys() for d in data_by_cfg.values()])))
    for k in all_keys:
        vals = [f"{cfg}: {data_by_cfg[cfg].get(k)}" for cfg in configs]
        unique_vals = set(data_by_cfg[cfg].get(k) for cfg in configs)
        diff_flag = " *** VARIATION ***" if len(unique_vals) > 1 else ""
        print(f"{k:26} | {' | '.join(vals)}{diff_flag}")
