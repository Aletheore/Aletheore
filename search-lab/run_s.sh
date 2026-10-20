#!/bin/zsh
# usage: run_s.sh <name> <brief> "<done files>"   retries a builder up to 3 times until the done files exist
cd "$(dirname "$0")"
n=$1; b=$2; shift 2
for try in 1 2 3; do
  python3 delegate.py $n $b > scratch/delegate_$n.try$try.log 2>&1
  ok=1; for f in ${=@}; do [ -f $f ] || ok=0; done
  [ $ok = 1 ] && break
done
echo "$n finished" >> scratch/done.txt
