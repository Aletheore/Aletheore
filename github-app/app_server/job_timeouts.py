"""Job timeouts shared by the webhook handlers that enqueue scan jobs and the scan worker."""

# One budget for every job that checks out a repo and scans it: the PR scan, the push scan (including the
# one after a PR closes unmerged), and the initial scan of a newly connected repo.
#
# It was 300s. A push scan of cal.com ran about 260s at the median and one hit the limit and was killed
# mid-clone (2026-10-30), because these jobs fetch the repo's full history and the scan walks all of it.
# A killed clone also leaves a half-written checkout behind. 1200s is about 4.5x the slowest observed
# success. It stays below the 1800s the AIRview/Docs builds get (which run on their own queue) and below
# the workers' 30m30s stop_grace_period, so a redeploy still waits for a running scan to finish.
SCAN_JOB_TIMEOUT_SECONDS = 1200
