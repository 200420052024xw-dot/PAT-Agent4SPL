# Archived PAT-Agent Materials

This directory preserves project components that are not part of the current
SPL/PAT integration work:

- `Interface/`: the original interactive web interface.
- `Experiments_Demo/`: generated models, traces, and timing demonstrations.
- `Datasets/`: the paper evaluation datasets.
- `Automated_Pipelines/`: the original full and no-planning pipelines.
- `Repair_Pipeline_Results/`: run records produced before the code reorganization.

The original full pipeline remains at
`Automated_Pipelines/Full_Pipeline/pipeline.py`. Current commands are implemented
under `../pat_agent/`; `../src/pipeline.py` is only a compatibility entry point.
Archived files retain their original path assumptions and should be treated as
reference/reproduction material.
