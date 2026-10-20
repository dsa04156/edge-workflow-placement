# 최소 실행 순서

1. EdgeFlow_ready_source.zip을 풀고 해당 폴더로 이동한다.
2. Python3.11과 EdgeFlow_runtime_assets.tar를 준비한다.
3. `python3 edgeflow.py import-assets /경로/EdgeFlow_runtime_assets.tar`
4. `python3 edgeflow.py setup-local --python python3.11`
5. `python3 edgeflow.py local-smoke --out output-local.json`
6. 장비 비교는 cluster.json 수정 후 `python3 edgeflow.py doctor`, `python3 edgeflow.py run` 순서로 실행한다.
7. 실행 폴더에 대해 `verify`, `report` 명령으로 결과를 확인한다.

상세한외부환경조건과검증범위는README.md를참조한다.
