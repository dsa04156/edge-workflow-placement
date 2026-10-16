# E1 준비 실험: 세 Task의 공동 위치 선택

설계일: 2026-10-16. 상태: 설계·source 동결, 실행 준비 중. [T2만 이동한 E0](e0-rpc-profile.md) 뒤에 사용자가 승인한 정적 배치 검증을 계속한다. kube-scheduler·Koordinator·논문 DAG 구현·DRL의 필수 비교 전체와는 구분한다.

## 실행과 비용 경계

같은 JPEG·원본 SqueezeNet·top-5 smoke 계약을 유지한다. T1 JPEG 전처리, T2 실제 분류 추론, T3 top-5 후처리의 위치를 각각 ARM 엣지 CPU·x86 서버 CPU·서버 GPU·서버 Intel NPU worker 중에서 고른다. T1/T3는 해당 worker의 CPU에서 실행한다. 입력은 엣지 client에 있고, 첫 worker로 JPEG 661,378byte를 보낸다. Task 간 payload는 T1→T2 602,112byte, T2→T3 4,000byte다.

서로 다른 worker면 직접 HTTP로 전달하며, 같은 worker면 함수 호출로 이어간다. 모든 정책이 같은 실행기를 쓴다. 비인접 Task의 worker가 같아도 A→B→A 전달을 허용한다. HTTP 통합 검사로 이 경로와 전부 같은 worker의 경로를 실제 CPU 추론으로 확인한다. 모든 TCP endpoint에 NODELAY1·HTTP1.1을 쓰고 조건 내 연결을 재사용한다. GPU/NPU는 검증한 원본/파생 준비 경로를 유지한다. 새 server와 client의 asset/SDK는 이전 임시 cache에서 SHA를 확인하며 준비한다.

Task 시간은 입력 hash/통신 뒤 실제 Task의 decode/전처리·추론·직렬화 또는 후처리 구간이다. GPU host/device 복사는 T2 안에 포함되며 별도 CUDA event를 더해 중복 계상하지 않는다. 각 stage는 자식 호출 wall time을 기록한다. 통신 및 실행기 잔여 비용은 다음처럼 분해한다.

- `c0 = client workflow wall − T1 task − T1 child RPC`
- `c1 = T1 child RPC − T2 task − T2 child RPC`
- `c2 = T2 child RPC − T3 task`

세 잔여 비용과 세 Task 시간을 합하면 해당 요청의 전체 시간을 복원한다. 잔여에는 네트워크 왕복·헤더/결과 반환·검증·실행기 처리 등이 포함되며 순수 one-way network 시간은 아니다. 같은 worker의 함수 호출 비용도 이 잔여에 남긴다. 상위 RPC가 포함한 하위 RPC를 다시 전체 시간에 더하지 않는다.

## 측정 전 고정한 설계

1. 4³=64개 배치를 seed20261017로 무작위 배정한다. 각 배치 warmup2·측정5: 보정용 448 workflow다. worker는 상주하며, 동시 workflow1개·최소 사이클0.25초다. 긴 처리의 pacing overrun은 제거하지 않는다.
2. 보정 로그에서 Task–worker별 Task 시간, ingress–T1 및 T1→T2/T2→T3 worker 쌍별 위 잔여 비용의 중앙값을 구한다. 세 정책 모두 이 같은 표를 받는다.
3. B2는 Task별 계산시간 최소, C1은 이미 정한 선행 위치로부터 전달 잔여+현재 Task 시간을 최소화하는 순차 greedy다. B3는 같은 표로 64개 배치의 합을 열거해 최소값을 고른다. 동률은 worker alias 사전순이다. B3는 **작은 DAG의 정확 열거**이며 JellyBean·Atlas 재현이나 온라인 최적해가 아니다.
4. 선택 결과·보정 로그 SHA·비용표를 evaluation 전에 동결한다. 비교 배치는 B2/C1/B3 및 전체 ARM·전체 GPU의 두 기준점이다. 같은 배치가 나오면 중복 실행하지 않고 여러 정책의 동일 선택으로 표시한다.
5. 별도 client Job3개에서 고정된 배치를 조건별 warmup2·측정10으로 평가한다. 순서는 seed20261117+block으로 배정한다. 같은 상주 worker를 공유하므로 독립 배포 반복이 아니다.

보고는 선택 배치, 예측/실측 오차, 블록별 중앙값·관측 범위·모든 실패와 pacing overrun을 포함한다. 요청을 독립 반복으로 세지 않고 p값·신뢰구간·p95 우위를 주장하지 않는다. 보정표로 충분히 해결되거나 C1과 B3가 같으면 그 사실을 그대로 보고한다. 한 입력의 warm 상태 파일럿이므로 새 알고리즘 필요성·데이터셋 품질·동적 전환 순이익을 확정하지 않는다.

## 재현 자료

- [실행기](../../experiments/e0/dag_profile.py), [HTTP 통합 검사](../../experiments/e0/test_dag_profile.py), [manifest 생성](../../experiments/e0/prepare_dag_profile.py)
- [동결 source·worker](../../artifacts/e1-dag/workers.json), [실행 정보](../../artifacts/e1-dag/run.json), [보정 배정표](../../artifacts/e1-dag/calibration-schedule.json)

기존 workload·driver·EdgeCore/CloudCore·클러스터 네트워크는 변경하지 않는다. 전용 리소스와 이전 run에서 재사용한 임시 cache는 증거 수집 뒤 정확한 이름으로 정리한다.
