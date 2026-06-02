# SkuldBot Runner

SkuldBot Runner is the local execution agent for SkuldBot automation workloads.
It registers with the Orchestrator, reports runner capabilities, polls for
assigned runs, executes packaged Robot Framework workloads, and reports
progress, logs, and completion status back to the Orchestrator.

This package is intended to run inside a customer-controlled execution
environment. Graphical runtime support is declared only when the runner can
detect an available display session; legacy desktop labels alone are not treated
as graphical capability.
