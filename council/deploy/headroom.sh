#!/bin/sh
# A READ-ONLY look at how much room a machine has. It writes nothing, changes nothing and starts nothing.
# Run it on the real server only with the operator's explicit go-ahead; its output is what sizing decisions are based on.
set -eu

echo "== host"
date -u
uname -a

echo "== cpu"
(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "unknown")

echo "== load (1, 5, 15 minute averages)"
(cat /proc/loadavg 2>/dev/null || uptime)

echo "== memory"
(free -m 2>/dev/null || vm_stat 2>/dev/null || echo "unknown")

echo "== disk"
df -h / 2>/dev/null || true

echo "== containers right now (cpu and memory)"
if command -v docker >/dev/null 2>&1; then
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || echo "docker not reachable"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}' 2>/dev/null || true
else
  echo "docker not found"
fi

echo "== listening ports"
(ss -ltn 2>/dev/null || netstat -an 2>/dev/null | grep LISTEN || echo "unknown")

echo "== busiest processes"
(ps -eo pid,pcpu,pmem,rss,comm --sort=-pcpu 2>/dev/null || ps -Ao pid,pcpu,pmem,rss,comm -r 2>/dev/null) | head -8

echo "== reverse proxy"
(command -v caddy >/dev/null 2>&1 && echo "caddy found: $(command -v caddy)" || echo "caddy not on PATH")
(systemctl is-active caddy 2>/dev/null || true)
