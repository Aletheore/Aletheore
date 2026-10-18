# Path C: Hardware or network faults `[High Risk]`

When the analyzer attributes the hang to faulty hardware, the network, or an
unrecoverable error on specific instances:

1. **Identify the culprit node**: Provide the `kubectl get nodes` command from
   Step 3 of `SKILL.md` to map the reported instance IDs to GKE node names, and
   review the analyzer report's `recommendedActions`.
2. **For `UNRECOVERABLE_ERROR`**: Inspect the error log of the reported
   instances. Per the analyzer documentation, if the error is specific to the
   machine, configure the job to avoid those hosts. Otherwise, the problem is
   likely at the application level.
3. **Handle node repair through GKE, not Compute Engine**:
   - Don't change the node's VM or the node pool's managed instance group
     through Compute Engine.
   - GKE node auto-repair repairs nodes that meet the
     [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
     which states that a node reporting a `Ready` status is considered healthy.
     None of the listed repair conditions, including the TPU slice conditions in
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
     mention ICI errors or DCN networking faults. Because a node involved in a
     hang can stay `Ready`, auto-repair might not catch this condition, so
     opening the support case in item 4 below is the main action.
   - If the node does transition to an unhealthy condition, Autopilot clusters
     always auto-repair nodes, and Standard cluster users can check or turn on
     auto-repair by following the "Verify node auto-repair is enabled for a
     Standard node pool" and "Enable auto-repair for an existing Standard node
     pool" sections of
     [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     (note per "Node auto repair in TPU slice nodes" in that document that GKE
     re-creates the entire multi-host TPU slice node pool when a node in it
     needs repair).
4. **Escalate to Google Cloud Support (main action)**:
   - Follow
     [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
     and open a support case with the `MonitoredEvent` resource name
     (`projects/{project_id}/locations/{location}/machineLearningRuns/{ml_run_id}/monitoredEvents/{event_id}`),
     the `Megascale XLA (MXLA) Hang Analyzer` report, and the mapped Compute
     Engine instance ID and GKE `{node_name}`.
