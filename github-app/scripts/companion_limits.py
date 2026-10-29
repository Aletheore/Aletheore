"""Operator-only administration over private Redis. Never called by the public API."""

import argparse
import json
import os
from redis import Redis

parser = argparse.ArgumentParser()
parser.add_argument("operation", choices=["initialize-first-use", "disable", "status"])
args = parser.parse_args()
url = os.environ.get("REDIS_URL")
if not url:
    raise SystemExit("REDIS_URL must be supplied in the server environment.")
client = Redis.from_url(
    url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
)
key = "aletheore:{companion-budget}:ledger"
if args.operation == "initialize-first-use":
    script = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'version','1','enabled','1','day','0','month','0','day_used','0','month_used','0','day_cap','20000000','month_cap','500000000')
return 1
"""
    if client.eval(script, 1, key) != 1:
        raise SystemExit(
            "Ledger exists. No state changed; never reset it to bypass accounting."
        )
    print("Initialized first-use ledger with INR 20/day and INR 500/month ceilings.")
elif args.operation == "disable":
    script = "if redis.call('EXISTS', KEYS[1]) ~= 1 then return 0 end; redis.call('HSET', KEYS[1], 'enabled','0'); return 1"
    if client.eval(script, 1, key) != 1:
        raise SystemExit(
            "Ledger missing. No state changed; reconcile provider billing before repair."
        )
    print("Companion model admissions disabled.")
else:
    state = client.hgetall(key)
    required = {
        "version",
        "enabled",
        "day",
        "month",
        "day_used",
        "month_used",
        "day_cap",
        "month_cap",
    }
    healthy = (
        required <= state.keys()
        and state["version"] == "1"
        and state["enabled"] in {"0", "1"}
        and all(
            state[field].isascii() and state[field].isdigit()
            for field in required - {"version", "enabled"}
        )
        and int(state["day_cap"]) <= 20_000_000
        and int(state["month_cap"]) <= 500_000_000
    )
    print(
        json.dumps(
            {"status": "present" if healthy else "missing_or_corrupt", "ledger": state},
            sort_keys=True,
        )
    )
    if not healthy:
        raise SystemExit(
            "Keep chat disabled. Reconcile accounting with provider billing before repair."
        )
