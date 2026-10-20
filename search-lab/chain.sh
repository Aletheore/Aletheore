#!/bin/zsh
cd "$(dirname "$0")"
while pgrep -f gen_intents.py >/dev/null; do sleep 20; done
python3 - <<'P'
import json
d={}
for l in open("data/intents.jsonl"):
    r=json.loads(l); d[r["skill_id"]]=r["intents"]
json.dump(dict(sorted(d.items())),open("data/intents.json","w"),indent=0)
print(len(d), sum(len(v)>=5 for v in d.values()))
P
echo "s1 finished" >> scratch/done.txt
./run_s.sh s2 s2_ranking.md search_lab.py lab_rankers.py data/queries_heldout.jsonl ../src/aletheore/skills/_intents.json
./run_s.sh s3 s3_findskill.md calibrate_confidence.py measure_tokens.py
