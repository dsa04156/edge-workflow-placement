# P2 수정 보고: 명시적 legacy 크기 topology의 preflight/동결 우회

대상 지적: `review-Spec.md`, `review-Standards.md`의 동일 P2 (중복 1건).

## 원인
`run_baseline_comparison.collect()`가 `topology-preflight` 실행 여부를 `not topology.is_legacy(session.topology)`(노드 집합 동일성)로 판정했다. 그래서 `--topology`로 e0/e1/c만 담은 JSON을 명시하면 기본값과 같다고 보고 Ready/cordon/taint 점검과 `topology-frozen.json`/`topology-preflight.json` 기록을 모두 건너뛰었다.

## 수정 (최소 범위)
- `experiments/real-topology-live/run_baseline_comparison.py:342-343`: 판정 기준을 `session.topology_config is not None`(명시적 설정 존재)으로 변경. 플래그 없는 implicit legacy 경로는 `use_topology(None)` → `topology_config=None`이라 기존과 동일하게 건너뛴다.
- `experiments/real-topology-live/test_ready_node_expansion.py`: `ExpandedPilot`/`run_expanded`에 `config`/`choice` 인자 추가(기본값은 기존 9노드 설정·CHOICE 그대로), `ExplicitLegacySizedTests` 3건 추가.
- 보조 파일(verifier, topology.py, design) 수정 없음.

## 회귀 테스트 (수정 전 먼저 작성)
실제 `collect()` → `Pilot.deploy/qualify_all/transfer_profiles` 경로, kubectl·업로드·RPC만 fake.
1. `test_unavailable_node_fails_before_any_mutation`: 명시적 3노드 설정에서 e0 NotReady / c cordoned / e1 NoSchedule taint → `Topology preflight failed`, apply/exec/delete 및 subprocess 0건, `scheduler-provenance.json` 없음, `topology-frozen.json` 존재, preflight `failed`.
2. `test_valid_run_freezes_verifiable_evidence`: 정상 명시적 3노드 → `topology-frozen.json`(topology=LEGACY, digest, config sha256 일치), preflight `passed`, `vb.verify` 오류 0·`passed`; preflight 파일 삭제 시 verifier가 `missing topology-preflight.json`으로 거부.
3. `test_implicit_legacy_skips_topology_preflight`: 플래그 없음 → `topology-nodes` 호출 없음, topology 산출물 없음, verifier 오류 0.

수정 전 실행: 1·2 실패(4개 subtest/test FAIL·ERROR, 리뷰어 재현과 동일), 3 통과. 수정 후 전부 통과.

## design/verifier 일관성 (explicit 3노드)
- `build_design`은 여전히 `is_legacy` 기준이므로 명시적 3노드 run의 `design.json`은 legacy design과 동일(topology 항목 없음). verifier도 `topology-frozen.json`의 topology로 `build_design`을 다시 계산하므로 일치한다. 설정 경로/sha256은 `topology-frozen.json`에 동결된다.
- verifier의 explicit 판정은 이미 `topology-frozen.json` 존재 여부 기반이며, 이 경우 `topology-preflight` phase와 `check_topology`(digest, preflight 재계산, raw 노드 snapshot 대조)를 요구한다. 테스트 2로 통과 확인 → 보조 파일 수정 불필요.

## 검증 결과 (`rtk proxy`, experiments/real-topology-live)
- 대상: `python3 -B -m unittest test_ready_node_expansion` → 23 tests OK.
- 전체 live: `python3 -B -m unittest discover -s . -p 'test_*.py'` → 88 tests OK (기존 85 + 신규 3), 20.8s.

## 보존/제약
- 수정 전 사본: `.scratch/ready-node-expansion/before-p2-fix/` (run_baseline_comparison.py sha256 `180bca99…a7de9dc`, test_ready_node_expansion.py `33ad390e…8ab86654`). 기존 `before/`는 건드리지 않음.
- 클러스터 접근, 의존성 변경, git 작업 없음. 기존 exec EOF live blocker와 추가 노드 런타임 미검증 상태는 변경 없음(레벨: 코드 존재 + 관련 단위 테스트 통과; 실장비 미실행).
