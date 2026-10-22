# Input fixtures

Small, deterministic catalog/detail inputs belong here. Large generated inputs
must be created inside the isolated test workspace.

`controlled_details_v1.csv` contains the stable positive detail case.
`controlled_details_missing_v1.csv` contains the explicit 404/not-found case.
Catalog -> detail acceptance uses the catalog run's isolated final CSV directly.
