# Expected fixtures

Versioned expected schemas, stable IDs and normalized rows for the controlled
site belong here. Volatile Ozon/WB prices and counts must not be stored as exact
expectations.

`controlled_dataset_v1.json` records the first verified site contract:
3 catalog pages, page counts `9/9/1`, 19 total products and stable boundary
products used by exact assertions.

`controlled_details_v1.json` records the exact positive enrichment values and
the source-row contract for a missing product. It deliberately excludes price
from exact assertions because price is not the product identity.
