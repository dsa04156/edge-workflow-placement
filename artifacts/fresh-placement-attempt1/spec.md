# 최신 보정표 동결 후 품질 제약 배치 평가

Status: ready-for-agent

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-16
- Verification Status: UNVERIFIED
- Version Label: fresh_placement_v1

## 수집 전에 고정한 계약

현재 PolicyRunner HTTP 경로에서 새 비용표를 보정하고 Task-fastest·데이터 인지 greedy·작은3Task DAG 열거의 **고정 배치**를 별도 시간 구간에서 평가한다. 논문 정책·새 알고리즘·동적 재배치 비교는 아니다. 기존8입력/모델/전처리/CPU reference와 ARM edge/Intel NPU 서버를 유지한다.

- 입력8개를 모두 사용한다. CPU reference의 **Top-5 순서까지 정확히 일치**하는 T2 후보를 보정 관측으로 정한다. 어떤 입력도 사후 제외하지 않는다. hash/shape/finite 통과와 품질 일치를 구분한다. CPU reference는 정답 라벨이 아니다.
- 한 입력·T2후보의 모든 보정 출력(warmup 포함)이 기준 Top-5와 같아야 해당 입력에서 추론 후보가 된다. T1/T3는 검증된 CPU 전·후처리 후보를 유지한다. 유효 T2후보가 없으면 실행 가능성 실패로 보고하고 평가를 강행하지 않는다. 평가 중 품질 불일치·실행 오류는 모두 기록하고 유효 완료 분모에서 제외하되 입력/시도 분모는 유지한다.
- 같은 host/날짜에서 worker2/client2를 순차3회 새 배포한다. Pod UID/컨테이너 ID를 확인하지만 driver/OS/image cache와 물리 환경의 독립성을 주장하지 않는다. 모든 모델 객체는 warm, 인위적 eviction·네트워크/driver 설정 변경은 없다.
- 배포마다 source2 × 입력8 ×8배치=128조건을 보정한다. 조건당 warmup1+측정3, 총512workflow/배포다. warm Task/ingress/간선 잔여 비용의 중앙값을 사용한다. RPC에 포함된 하위 실행을 중복 합산하지 않으며 잔여값을 순수 네트워크 시간으로 부르지 않는다.
- 두 source 보정이 모두 끝난 뒤 비용표·품질 후보·정책별 선택 계획·선택 계산시간을 파일과 immutable ConfigMap으로 동결한다. 이후 평가 관측으로 재선택/튜닝하지 않는다. 같은8입력의 시간상 holdout이며 새로운 입력 일반화 검증은 아니다.
- 모든 정책은 동일 profile·후보를 사용한다. Task-fastest는 Task 비용만, greedy는 현재 ingress/선행 간선+Task 비용만, DAG는3Task 전체 합을 최소화한다. 동률은 alias 사전순이다. 준비 비용 상각/look-ahead·동적 cache 정책은 넣지 않는다.
- 같은 입력/source에서 같은 계획을 고르면 실행 관측을 공유한다. 정책 선택은 평가 전1회이고 선택시간을 별도 보고한다. 평가 callback은 고정 계획 적용만 수행한다. 따라서 per-request total_ms는 공통 검증·health·계획 조회·상태 재확인·실제 HTTP·receipt 검증 비용이며 정책별 재최적화 시간을 포함한 동적 비용 비교가 아니다.
- 각 고유계획을 warmup2+측정5회 평가한다. 고유계획 수는 보정 뒤1~3개/입력/source로 결정되며 평가 응답을 보고 정하지 않는다. 배포당 평가112~336workflow/측정80~240회,3배포 합계 전체1,872~2,544workflow다. 같은 계획을 독립 정책 표본으로 중복 세지 않는다.
- source는 whole-plot 구간으로 묶는다. experimental-design의 seeded `assign_factorial_runs`로 보정 source 순서(seed20261101+block), 보정 source내128/2조건(seed20261111+10*block+source_index), 평가 source 순서(seed20261121+block), 평가 source내고유계획(seed20261131+10*block+source_index)을 고정한다. 최소 주기0.25초, 초과/오류 보존.
- 주 지표는5측정 episode의 평균 total_ms와 유효 완료 수다. 입력/source/배포별 paired 차이와 선택계획 일치 여부를 모두 보고한다. raw n/mean/median/SD/range·결측/오류를 보존한다. 단일 날짜3배포의 기술 통계이며 p값/CI/p95/SLO·통계적 우위/동등성은 주장하지 않는다.
- SDK/Pod시작·보정 획득·1회 최적화·배치준비·평가를 구분해 실제 시간을 별도 기록한다. 전체 준비 비용을 숨긴 운영 순이익 결론을 만들지 않는다. 기존 리소스 보호·정확한 소유 이름/label 정리·읽기 전용 EdgeCore 관측은 이전 경로를 따른다.

## 구현·검증 경계

기존 합의된 실제 HTTP DAG 경계에서 warm policy 선택과 품질 후보 동결을 red/green 확인한다. 새 모델/SDK/API는 도입하지 않고 기존 hash 고정 assets·image·14wheel을 재사용한다. ARS는 실행/모니터링 단계만 적용하며, 이미 사용자가 승인한 보정·평가 구현/실행 범위에서 필요한 스크립트를 작성한다. 실패 시 원인/시도를 보존하고 조용히 재실행하지 않는다.
