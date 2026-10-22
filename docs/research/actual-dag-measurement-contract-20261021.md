# 실제 DAG의 측정 항목·배치 경로 대응 계약

2026-10-21. 정적 배치 범위에서 다음 단계 진행. 동결 자료의 오프라인 전수 대조 완료; 새 정책 구현·클러스터 수집·통계적 성능 검증 없음.

## 결론

기존 합성 비용식을 실제 DAG로 바꾸려면 **품질 검증에서 이미 얻은 연산 관측을 공유하고, 실행 가능 배치에 필요한 전달 항목을 대상으로 추가 측정을 선택**해야 한다. 기존 실측 구성에는 모든 배치·모든 경로에서 동일하게 더해지는 primitive가 없다. 공통 비용 하나를 크게 만든 합성 사례를 이 환경의 대표 사례로 사용하지 않는다.

## 자료와 검증 범위

- 동결 run: `artifacts/topology-profile-live/wan-20261021t044624`.
- 입력: `profile-b0-compressed-{10,2}.json`, `transfer-calibration-{10,2}.json`, `qualification-all.json`, 원시 transfer response.
- [전수 대조 결과](../../.scratch/scenario-redesign/primitive-audit.json): SHA256 `377429123de466aace50c758f0aed76d5a5449e2b4e893e46e0c5fbc6409085c`.
- 현재 `prepare.py`는 동결 `planner-source.json` hash와 일치한다. `run_placement.py`, `edge_client.py`, `http_runtime.py`, `budget_placement.py`, `placement_policies.py`도 동결 `git-source.json`과 일치한다. 따라서 이 코드로 해당 자료의 의미를 추적했다.
- 각 조건의 27개 조합 중 실행 가능한 19개 모두에서 아래 확장 경로식과 기존 `prepare.cost(..., 'topology_profile')`의 차이 0.0ms. 총 38개 배치·조건 대조다. 이것은 비용식의 대응 검증이지 실제 종단 지연 예측의 정확성 검증이 아니다.

## 노드·자원·실행 가능 집합

배치 표기는 V/S/F 순서다. E0=`etri-dev0003-raspi5`, E1=`etri-dev0002-raspi5`, C=`etri-ser0002-cgnmsb`. 입력 source는 이미지 E0, 음성 E1에 고정한다.

각 작업 요청은 CPU 1,010m, 메모리 2,080MiB다. 동결 잔여 CPU는 E0 3,090m, E1 1,905m, C는 연구용 슬롯 2개를 적용한 2,020m다. 이 조건의 유효 배치는 E0 최대 3개, E1 최대 1개, C 최대 2개 작업을 허용한다. 메모리는 이번 27조합에서 추가 제외를 만들지 않는다.

E1에 두 작업 이상 놓이는 7개 조합과 C/C/C 1개를 제외하여 19개다. 이 집합은 해당 자원 snapshot에만 유효하며 새 수집 때 다시 동결한다. 서버 제한은 물리 포화가 아니다.

## 측정 항목 대응표

항목 ID에는 아래 종류·origin·destination 또는 task·node 외에 실제 수집 manifest의 모델/산출물 hash, 입력 집합·representation, runtime, 경로 및 환경 조건을 연결한다. 동일 링크라도 payload 종류가 다르면 독립적인 항목으로 유지한다. 같은 HTTP 요청을 중복 조회한 결과를 새 표본으로 세지 않는다.

| 항목 | 원시 관측 | 기존 profile 위치 | 개수 | 다음 측정 비용에 포함할 경계 |
|---|---|---|---:|---|
| `compute:<V/S/F>:<node>` | qualification receipt의 `compute_ms` | `tasks[].compute_ms[node]` | 9 | 기존 필수 검증 관측은 공통 초기 정보; 추가 probe는 준비·실행·제어 wall time |
| `transfer:image:E0|node` | `/transfer_profile` 응답 `samples_ms` | V source input의 `transfer_ms` | 3 | source 측 sink RPC 및 실제 취득 제어 시간 |
| `transfer:audio:E1|node` | 동일, 음성 payload | S source input의 `transfer_ms` | 3 | 동일 |
| `transfer:vision:node|node` | 동일, V 출력 payload | F의 V input `transfer_ms` | 9 → 유효 8 | 동일; V/F를 함께 E1에 놓는 항목 제외 |
| `transfer:speech:node|node` | 동일, S 출력 payload | F의 S input `transfer_ms` | 9 → 유효 8 | 동일; S/F를 함께 E1에 놓는 항목 제외 |
| `transfer:return:node|E0` | 동일, F 출력 payload | 아래 control 항목과 합쳐 `outputs[0].transfer_ms`에 저장 | 3 | 실제 결과 RPC가 아니라 payload echo proxy임을 유지 |
| `transfer:control:E0|node` | 동일, `{}` 2바이트 payload | 위 return 항목과 합산 | 3 | 실제 `/open`의 직접 측정이 아니라 작은 payload proxy임을 유지 |

총 39개(연산 9 + 전달 30)를 개별 관측에 대응시킨다. 원래 profile은 return/control을 묶으므로 36개 수치 항목이다. 실행 가능성으로 불필요한 전달 2개를 제거하면 **연산 9 + 전달 28 = 37개 활성 항목**이다.

전달 시간은 순수 one-way network delay가 아니라 직렬 payload 송신과 sink 응답·처리를 포함한 RPC proxy다. raw response의 `payload_bytes`는 입력별 실제 bytes이고, profile의 `bytes`는 평균이다. 평균 bytes로 원시 표본을 복원하지 않는다.

## 경로식과 실제 실행기 차이

V, S, F의 실행 노드를 v,s,f라고 하면 기존 목적함수를 정확히 확장한 식은 다음과 같다.

\[
L(v,s,f)=\max\{I(E0,v)+C_V(v)+D_V(v,f),\ A(E1,s)+C_S(s)+D_S(s,f)\}
+C_F(f)+R(f,E0)+O(E0,f).
\]

I/A는 source 전달, D는 분기 출력 전달, R은 반환 payload proxy, O는 control proxy다. 각 값은 기존 보정의 p75 요약값이며, 두 경로에 공통인 tail은 max 바깥에 한 번 더한다.

실제 `edge_client.run_once`는 `/open` 완료 후 V 실행과 E1 `/branch` 요청을 병렬 시작한다. 두 분기 응답 후 `/result`를 가져오고 `e2e_ms`를 기록한다. `/ack` 이후의 값은 `source_total_ms`로 별도 기록한다. `http_runtime`의 두 번째 `/join`은 F 실행 완료까지 기다리므로 분기 RPC 안에 F 실행이 포함될 수 있다. 그 RPC 전체 시간에 F compute를 다시 더하면 중복 계산이다.

현재 proxy에는 E0→E1 분기 시작 제어, 실제 응답 envelope, 시작 시차, queue·공유 링크/노드 경합의 정확한 분해가 없다. `/ack`도 E2E 목표 밖이다. 따라서 위 식을 실제 실행 timeline의 정확한 항등식으로 주장하지 않는다.

또한 기존 `run_placement.quantile`은 선형 보간 p75다. 순차 DKW 구간의 empirical generalized inverse와 구분한다. 기존 점 추정을 구간 생성 함수에 그대로 대입하지 않으며, raw 표본·추정 방법을 기록한다.

## 전수 대조에서 확인한 시나리오 구조

| 정적 조건 | 모형 1위 | proxy ms | 2위 | 1–2위 gap ms |
|---|---|---:|---|---:|
| 10Mbps | E0/C/C | 956.954 | E1/C/C | 198.648 |
| 2Mbps | E0/C/C | 952.991 | E1/C/C | 196.956 |

두 조건 모두 19개 배치 중 영상 경로가 긴 배치 7개, 음성 경로가 긴 배치 12개다. 최상위 배치의 임계경로는 영상이다. 따라서 fork/join 구조 자체는 존재하지만, 이 두 조건 사이에서 최선 배치나 최선 배치의 임계경로가 전환됐다는 근거는 없다. 모형 gap을 실제 성능 차이나 모집단 gap으로 해석하지 않는다.

모든 38개 경로(19배치 × 2경로)의 primitive 교집합은 각 조건에서 공집합이다. 반면 F가 같은 두 배치에서는 F 연산·return·control tail이 공통일 수 있다. **전체 후보 공통항 제거와 특정 배치 쌍의 상쇄는 다른 효과**다. 공유 분기 전체가 max 안에 있는 경우는 무조건 제거할 수 없다.

## 비교 계약의 구체적인 수정

1. **필수 검증 관측을 공유한다.** 기존 qualification에는 각 작업·노드당 calibration compute 표본 20개, 총 180개가 이미 있다. 모든 측정 정책이 이 정보를 받는다. 품질 검증 비용은 전체 준비 비용에 공통으로 남기며, 공짜 수행으로 간주하지 않는다. 재사용 표본의 불확실성은 남으므로 참 연산 비용을 아는 것으로 처리하지 않는다.
2. **불가능한 배치에만 필요한 probe를 먼저 제거한다.** V E1→E1, S E1→E1은 어떤 실행 가능 배치에서도 쓰이지 않는다. 이 제거를 모든 acquisition 기준선에 동일하게 적용한다. 기존 collector는 전달 30종류 × 20입력 = 조건당 600개 응답을 수집했다. 같은 20회 규칙에서 유효 28종류만 수집하면 560회라는 산술 비교가 되지만, 아직 wall-time 절감 실측은 없다. 이 제거 자체를 후보 알고리즘의 고유 성과로 세지 않는다.
3. **첫 오프라인 단계는 조건부 문제로 한정한다.** 연산 비용을 동결 profile 값으로 놓고 전달 측정 선택과 경로 대응을 검증할 수 있다. 이 경우 인증 명칭은 동결 연산값에 조건부인 proxy 인증이다. 모집단 연산 비용까지 포함한 인증으로 주장하지 않는다. 전체 보장을 평가하려면 9개 연산 항목에도 재사용 표본으로 유효 구간을 만들고 필요시 추가 측정을 허용해야 한다.
4. **총 취득 시간과 primitive 값은 다르다.** `samples_ms`는 probe 내부의 sink RPC 시간이다. collector event의 `started_at`/`finished_at`은 해당 제어 요청 elapsed time을 확인하는 보조 근거다. 새로운 실행기는 episode monotonic clock으로 준비·probe·계획의 중복 없는 전체 elapsed time을 기록한다. 개별 primitive p75를 probe 비용으로 대체하지 않는다.
5. **상태 유지 비용을 정의한다.** 모든 모델을 항상 준비해 두는 가정은 슬롯2 환경과 다를 수 있다. 기존 qualification도 작업·노드별 배포·제거를 수행한다. 추가 compute probe가 필요하면 배포/준비·교체 비용과 사용 자원을 관측 계약에 포함한다. 기존 자료로 해당 비용을 0이라고 추정하지 않는다.
6. **기준선은 동일 후보·초기 정보·예산으로 비교한다.** 전체 측정, 유효 항목 순환, 공통항 제외 순환, 폭/취득비용, witness, contrast를 비교한다. 이 전체 후보 집합에서 전역 공통항 제외 순환은 유효 항목 순환과 같은 probe 집합을 가진다. 선택 규칙만 다른 효과와 명목상 중복 기준선을 구분한다.

## 후속 구현의 수용 기준

- frozen profile → primitive 목록·두 경로/배치·실행 가능 집합을 변환하는 **오프라인 어댑터**부터 만든다. 기존 live collector, runtime, frozen artifacts는 수정하지 않는다.
- 입력 profile에 admission budget을 실제로 반영했는지 확인한다. 예산 없는 `profile-calibration-*`을 그대로 읽어 C/C/C를 허용하지 않는다.
- 10/2Mbps 각 19배치와 원 planner의 비용·최선 배치가 일치해야 한다. return/control 분리 시 원래 합과 일치해야 한다.
- 유효 37항목, 미사용 전달 2항목, 전역 공통항 0개, 초기 compute 180관측의 provenance를 출력한다. 추가 고정 표본 수나 미래 raw 값은 정책에 미리 노출하지 않는다.
- raw replay는 시간순서·입력 ID·조건을 보존한다. 동일 네트워크에서 관측된 항목들을 독립 난수로 재합성한 경우는 별도 가정 시뮬레이션으로 표시한다. 이 자료만으로 새로운 독립 episode 성능·i.i.d. 인증을 검증했다고 보고하지 않는다.
- 신규 수집 전에는 성능 유지 허용치·실질적 시간 절감·전체 episode 반복 수·무작위화 일정을 별도로 동결한다. 이번 대응 검증으로 그 수치를 확정하지 않는다.

실험 단위·개발/평가 분리 원칙과 설계 절차 출처는 [시나리오 재설계 문서](scenario-redesign-20261021.md)를 따른다.
