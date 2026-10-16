# E2 실측 비용표의 공통 정책 연결·KubeEdge 적용 검증

실행일: 2026-10-16. run `20261016t100338960422`. [수집 전 명세](../../.scratch/measured-policy-bridge/spec.md), [동결 manifest](../../artifacts/measured-policy-bridge/run.json), [검증 결과](../../artifacts/measured-policy-bridge/verification.json)를 따른다. **같은 snapshot에서 여섯 정책의 결정60건을 확인하고 고유 대표 배치10회·준비용2회를 실제 실행했다.** 성능 우위나 새 알고리즘 필요성을 평가한 실험은 아니다.

## 1. 이번에 연결한 것

앞선 [공통 PolicyRunner](common-policy-runner.md)의 synthetic 비용표를 대신해, E2의 실제 dog/source별 Task·ingress·간선 비용표를 그대로 제공했다. [변환·정책 연결](../../experiments/e0/measured_policy_bridge.py)은 다음을 보존한다.

- E2 `selection.json` SHA256은 `b7882f7f0bd7a68b5052d77ec9ea9b9986fba83528b6773a6abb946e25bbd373`이다. 당시 E3 manifest에 동결된 E2 selection hash와 다시 대조했다. 보정 로그2개의 hash, 실제 사용하는 `dynamic_client.py`·`probe.py`와 E2 ConfigMap/source hash도 확인했다.
- Task6칸·ingress2칸·간선별4칸/source의 값을 바꾸지 않았다. NPU 서비스 객체 준비 추정값은 E2의27.011536993086338ms, ARM 재준비 추정값은 미측정 `null`이다. **과거 비용표이며 이번 환경의 최신 비용표가 아니다.**
- E2 fixed/warm-DAG/ready-DAG-H4/ready-greedy-H4/credit-hysteresis는 기존 `dynamic_client.choose`를 그대로 사용한다. H4는 요청 간 비용 상각이며 AnchorDL의 DAG look-ahead로 부르지 않는다.
- 별도 Task-fastest는 공통 후보 가운데 각 Task의 예측 실행시간만 최소화하고 동률은 alias 순으로 결정한다. 필요한 Task 비용이 미측정이면 오류로 남긴다. 전달·준비 비용은 이 기준선의 선택식에 넣지 않는다.

[변환 계약 JSON](../../artifacts/measured-policy-bridge/bridge-contract.json)은 현재 source의 profile과 초기 계획·준비 추정값/출처만 정책에 연결한다. 미래 source/eviction 일정이나 E2/E3 평가 결과를 정책 입력으로 전달하지 않는다. E2 adapter는 원래 후보 집합과 비용표가 변경되면 거부한다. 후보 축소·새 품질 제약에 맞춰 원 정책을 확장한 구현은 아직 아니다.

## 2. 실제 실행 조건

ARM edge `etri-dev0003-raspi5`와 Intel NPU 서버 `etri-ser0002-cgnmsb`에 전용 worker/client를 배포했다. 기존 SHA-pinned image·14개 SDK wheel·3Task HTTP 실행기를 재사용했고, 실행 전 NPU 할당 요청0과 기존3개 workload의 Running/Ready를 확인했다. [사전 점검](../../artifacts/measured-policy-bridge/preflight.json), [배포 manifest](../../artifacts/measured-policy-bridge/deployment.json), [Pod 상태](../../artifacts/measured-policy-bridge/pods-after.json).

입력은 기존 hash 고정 dog 이미지와 SqueezeNet이다. T1은 CPU 전처리, T2는 ARM CPU 또는 Intel NPU 추론, T3는 CPU Top-5다. 아래 `N`은 **NPU 서버 worker**의 위치를 뜻하며 T1/T3까지 NPU에서 계산한다는 뜻은 아니다.

source2 × warm/서비스 객체 eviction2를 고정 순서로 확인했다. 각 적용 시도에서 새로운 읽기 전용 snapshot 하나를 만들고 그 **같은 snapshot ID**를 여섯 정책에 제공했다. 정책 객체는 시도마다 초기 상태로 생성했다. 따라서 hysteresis의 첫 결정은 검증하지만 장기 credit 누적·전환 궤적은 검증하지 않는다.

같은 계획을 고르는 정책은 대표 하나만 적용했다. 엣지 입력은 고유2배치, 서버 입력은3배치이므로 두 상태에서 총10회다. warm 상태를 만드는 별도 workflow2회도 남겼다. eviction 조건의 적용 시도마다 전용 NPU 객체를 해제했고 제어 호출5회는 workflow 종단 시간에서 제외한다. Core/plugin·driver/OS cache와 로컬 모델 파일은 유지한다.

## 3. 결정 보존과 데이터 경로

`A=ARM worker`, `N=Intel NPU 서버 worker`이며 각 셀은 T1→T2→T3 위치다. 아래 계획은 warm/eviction 모두 같았다. 각 정책의 전체 결정·snapshot·실행 결과는 [시도 JSON](../../artifacts/measured-policy-bridge/attempts.json)에 있다.

| 정책 | ARM edge 입력 | NPU 서버 입력 |
|---|---|---|
| E2 초기 고정 | A→A→A | A→A→A |
| E2 warm-DAG | A→A→A | N→N→N |
| E2 ready-DAG-H4 | A→A→A | N→N→N |
| E2 ready-greedy-H4 | A→A→A | N→N→N |
| E2 credit-hysteresis, 초기 상태 | A→A→A | N→N→N |
| Task-fastest | N→N→N | N→N→A |

서버 입력의 Task-fastest가 T3를 ARM으로 고른 이유는 해당 과거 Task 비용표의 T3 예측값이 ARM0.10258ms, NPU 서버 CPU0.18888ms이기 때문이다. E2 DAG/greedy 계열은 전달 비용도 포함해 T3를 서버에 둔다. **Task 자체의 작은 비용 차이와 데이터 경계 비용이 다른 선택을 만들 수 있음을 연결 수준에서 확인한 것이며, 이번 수집의 성능 이득 크기를 뜻하지 않는다.**

총12 workflow의 요청/입력/계획·node/backend·payload hash와 [ARM worker receipt](../../artifacts/measured-policy-bridge/worker-arm.log)·[NPU worker receipt](../../artifacts/measured-policy-bridge/worker-intel_npu.log)36개를 대조했다. 실제 NPU 추론8회, ARM CPU 추론4회였고 모든 결과의 dog Top-5는 `[258,259,157,279,152]`였다. 실제 T2 NPU 결과의 `execution_devices=['NPU']`, T1/T3와 ARM T2의 CPU 증거를 확인했다. protocol/workflow 실패0이었다.

객체 해제 뒤 실제 NPU 재준비3회는25.85/47.63/46.18ms였다. 과거 보정27.01ms와 실제 준비 시간이 항상 같지 않다는 경계는 유지한다. 표본3회로 분포·원인·성능 우위를 추정하지 않는다. E3의 camera Top-5 차이도 이 단일 입력 일치로 해소된 것이 아니다.

## 4. 검증·재현

실제 실행한 명령이다. 수집 스크립트는 기존 산출물을 덮어쓰지 않으므로 재실행은 새 작업 사본에서 수행한다.

```bash
rtk proxy uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with onnxruntime==1.23.2 --with Pillow==11.3.0 python -m unittest discover -s experiments/e0 -p 'test_*.py'
rtk proxy python3 experiments/e0/run_bridge_validation.py
```

[HTTP red/green 기록](../../artifacts/measured-policy-bridge/tdd.json)은 실측 profile의 기존 선택 보존, 혼합 Task-fastest 경로, 미측정 Task 비용 거부의 세 slice를 포함한다. 테스트 보강 중 성공 케이스 검증문이 오류 케이스에 들어간 실수를 수정한 기록도 보존했다. 수정 후 **전체22개 테스트가 통과**했다. 기존/상속된 계약 검사가 포함된 수이며 독립 실험22회를 뜻하지 않는다.

로컬 테스트에서는 E2의 논리 alias를 사용하지만 두 실제 backend는 CPU로 기록한다. 실제 NPU 검증은 위 별도 클러스터 실행이다. [수집 스크립트](../../experiments/e0/run_bridge_validation.py)는 각 source의 입력 명세·stdout/stderr를 보존하고, live immutable ConfigMap의 source가 동결 내용과 같은지 확인한 뒤 소유 이름/label을 대조해 정리한다.

관측·profile 검증·여섯 정책의 결정 비교/검산·적용 전 상태 확인·HTTP 실행/receipt 검증이 `total_ms`에 들어간다. 이 callback의 결정 시간은 여섯 정책을 함께 검사한 시간이므로 개별 정책 overhead로 해석하지 않는다. SDK/Pod 준비·warm 설정·외생 eviction 제어·client exec·파일 기록은 해당 지표에서 제외한다. 실행 원시 지연은 보존했지만 policy별 배정/장기 상태/최신 보정·반복 설계가 없으므로 지연 우월성·p95/SLO·일반화 수치를 제시하지 않는다.

## 5. EdgeCore와 정리

[사후 EdgeCore 관측](../../artifacts/measured-policy-bridge/edge-snapshot.json)은 `10:05:03UTC`에 EdgeCore1프로세스, RSS112,800kB,15threads, StateS와 eth0 up·carrier1·100Mbps/full을 확인했다. NIC 협상값은 실효 throughput·packet 경로·기존 watch 오류 원인 확인이 아니다.

[정리 결과](../../artifacts/measured-policy-bridge/cleanup.json)는 소유 리소스 잔여0, 임시wheel14개 삭제를 확인했다. namespace와 측정 증거를 보존했고 [기존3개 workload는 Running/Ready](../../artifacts/measured-policy-bridge/preexisting-after-cleanup.json)였다. driver·EdgeCore/CloudCore·NIC/네트워크 설정은 변경하지 않았다.

현재 폴더는 Git 저장소가 아니어서 [code-review 스킬](/home/jinuk/.agents/skills/code-review/SKILL.md)의 “confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty”를 충족하지 못한다. Git 차분 두 축 리뷰 완료로 표시하지 않으며 실제 HTTP·동결 source·양끝 receipt를 검증 근거로 남긴다. 새 외부 API/SDK를 도입하지 않았고, 공통 연결부에서 확인한 공식 CPython HTTP 계약과 기존 검증된 배포 경로를 재사용했다.

## 6. 다음 연구 판단

**완료한 것은 실측 표의 연결, 동일 snapshot의 결정 호환성, 실제 배치 적용이다.** 최신 profile을 받은 DAG가 greedy보다 낫다거나 새 정책이 필요하다는 근거는 추가하지 않는다. 고정 과거 표에서 네 적응 정책의 선택은 여전히 같았다.

다음 본 비교에는 현재 실행 경로의 비용표를 별도 보정하고 평가 구간 전에 동결하는 단계가 필요하다. 입력별 품질·후보를 먼저 정하고, Task-fastest/greedy/작은 DAG 열거를 같은 profile과 고정 평가 입력으로 비교한다. 여러 추론 모델·분기/합류와 cache 경쟁, 실제 링크/호환성 변화는 별도 확장 과제다. AnchorDL은 [원문 비용식·look-ahead·cache·baseline 계약](../research/anchordl-reproduction-contract.md)이 여전히 필요하며 이번 E2 연결을 원문 재현으로 부르지 않는다.

이후 이 단계의 보정/평가 분리는 [fresh v2 정적 비교](fresh-placement.md)로 완료했다. 새 보정표·품질 후보를 동결하고 별도 평가679회/측정485회를 수행했다. 현재의 다음 검증과 역전 사례는 해당 후속 문서를 따른다.
