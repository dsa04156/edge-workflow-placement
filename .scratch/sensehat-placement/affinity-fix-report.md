# stage_pod nodeAffinity admission 수정 보고 (2026-10-21)

- 원인: `artifacts/sensehat-placement/sensehat-20261021t114403z` 첫 실제 실행에서 server dry-run 거부. `matchFields` `metadata.name` `In`은 값이 정확히 1개여야 하는데 scheduler-probe Pod가 후보 2개(edge, server)를 한 항목에 넣었다.
- 수정(`experiments/sensehat-placement/run.py` `stage_pod`): 후보마다 `nodeSelectorTerms` 항목 1개(OR), 각 항목 `matchFields metadata.name In [단일 노드]`. 단일 노드 pool Pod는 항목 1개로 동일. 후보 집합·순서, 요청/이미지/모델/runtime 불변.
- fake(`fake_kubectl.py`): 모든 terms를 OR로 합쳐 후보 결정(첫 항목만 읽지 않음). API server처럼 값≠1이면 apply(dry-run 포함) 거부, exit 1.
- 회귀 테스트(`test_run.py::StagePodAffinity`): 모든 term이 field 값 1개, scheduler 후보 총 2개·순서 불변, pool Pod 항목 1개.
- 검증 레벨: 코드 + 로컬 테스트 `test_run`, `test_execute` 22개 통과. 실제 클러스터 dry-run/실행은 미수행(코디네이터가 fresh prepare/run 재시도). 원래 실패 artifact는 수정하지 않음.
