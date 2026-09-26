# The campaigns that were actually run

Each script here is the exact invocation of one campaign, kept so that a number
can be traced back to the run that produced it rather than to a description of
it. They are records first and reusable second: they name the build
directories, corpora and file lists of the machine they ran on, and several
wait on each other (`cnf-rung.sh` runs after `eq-policy.sh`, which runs after
`const-policies.sh`) because they shared one box.

To re-run one somewhere else, the harness's own `configs/local.yaml` redirects
the solver binaries (see the main README); the corpora and output paths in
these scripts are edited or overridden on the command line.

| | what it ran |
| --- | --- |
| `recapture-v3.sh` | the whole pipeline: capture the three STP arms with the fixed KLEE, inventory, curate `bench-v3`, prove the float-to-bits bindings intact, then every campaign the draft's tables come from |
| `resume-v3.sh` | the tail of the above, from the binding check on, for resuming after a stop |
| `run-v4.sh` | the v4 campaign: five arms over `bench-v3`, three runs at 60 s with revalidation — the one that carries the Bitwuzla arm |
| `run-v5.sh` | v5: four STP arms into a fresh directory |
| `run-v5-trail.sh` | the trail-reuse ablation: the two abstraction arms at both settings |
| `run-v6.sh` | the final campaign: every configuration crossed with `--refinement-trail-reuse`, in one pass |
| `run-v6-followup.sh` | three follow-ups to v6, cheapest and most informative first |
| `old-vs-new-full.sh` | the diagnostic A/B of two optimised builds, with and without the levers |
| `b128-bisect.sh` | splits the binary128 regression, after waiting for v4 to finish |
| `const-policies.sh`, `auto-policy.sh`, `policy-defaults.sh` | the constant-operand policies, crossed and then as defaults |
| `eq-policy.sh`, `eq-onoff.sh` | the equality abstraction: constant-side policy, then on/off in the FP arms |
| `cnf-rung.sh` | the CNF rung on the converted QF_FP corpus |
| `certora-constant-operands.sh` | whether a record for a wide operation with a constant operand pays, on Certora |
| `levers-after-ab.sh` | the levers campaign, queued behind a running A/B |
| `score_ab.py` | scores a one-run A/B per width and per arm pair: solved within VT, PAR2 charging 2×VT otherwise |

The campaign outputs themselves -- CSVs, logs, manifests -- are not here. They
are large, they belong with the analysis that reads them, and the manifest each
run writes records the solver revisions, so a CSV without its manifest is not
worth keeping anyway.
