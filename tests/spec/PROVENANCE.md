# Released Principle interface

`src/dental_practice_admin/principle_openapi.json` is an example-free normalized interface
from the published OpenAPI document at https://api.principle.dental/assets/api.yml.
Vendor prose and examples are excluded. The original YAML stays ignored.

`scripts.refresh_spec --update` fetches and shows a structural diff for review. `--check`
validates offline; `--staged` validates the staged snapshot; `--upstream-check` compares
published content. FastMCP consumes the snapshot directly. No generated artifact is maintained.

The upstream `info.version` has remained unchanged across added paths; it is not a compatibility
signal. Pagination semantics remain verified by `test_pagination_contract.py` and the README's
contract table. `meta.total` is not a whole-practice patient count.
