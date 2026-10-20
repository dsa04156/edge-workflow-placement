# 서버 작업 슬롯 제한에서 greedy와 DAG 공동 배치: 부분 결과

**전체 실험 미완료.** 최신 실행 `wan-20261020t053336`은 첫 10Mbps 블록의 세 배치 평가를 마친 뒤 설정 복구 명령의 `unexpected EOF`로 중단됐다. 완료 구간의 자료 감사는 통과했지만, 2Mbps 대조·두 번째 시간 블록·마지막 복구 확인은 완료하지 못했다. 수집 코드 `df0bafd`.

서버에 허용하는 이 워크플로의 작업 슬롯을 2개로 제한했을 때, 첫 블록에서 정확 열거 배치는 greedy보다 평균 요청 지연이 **16.80% 낮았다**. 단순 한 번 교환도 **12.08% 낮아**, 관측된 차이의 약 **71.91%**를 줄였다. 한 블록의 기술 통계이며 반복성·통계적 우월성·새 알고리즘 필요성을 입증하지 않는다.

| 정책 | 영상 / 음성 / 결합 | 평가 성공 | 평균 E2E |
|---|---|---:|---:|
| greedy / HEFT 상주 서비스 변형 | C / C / E1 | 6/6 공유 관측 | 1088.86ms |
| DAG 정확 열거 (`ours`) | E0 / C / C | 6/6 | 905.97ms |
| greedy 작업 쌍 한 번 교환 | E1 / C / C | 6/6 | 957.34ms |

E0=`etri-dev0003-raspi5`, E1=`etri-dev0002-raspi5`, C=`etri-ser0002-cgnmsb`. 이미지 source는 E0, 음성 source는 E1로 고정했다. 영상·음성·결합은 ResNet18/Wav2Vec2/MUTAN CPU 실행이다. 같은 배치를 선택한 greedy와 HEFT는 같은 6건을 공유하며 독립 표본으로 중복 계산하지 않는다. 교환 배치와 정확 열거의 관측 차이는51.37ms이며 단일 블록 변동과 프로파일 오차를 분리하지 못했다.

## 조건과 보정

이 제한은 **워크플로 전용 admission budget**이며 물리 서버 포화나 실제 경쟁 부하를 재현한 것이 아니다. 각 작업의 요청은 netctl 포함1010mCPU/2080Mi, Torch thread1이다. 선택기는 같은 비용표·실행 후보·제약을 받고, 실행기는 선택한 전체 배치의 예산 준수를 검사한다. filler나 호스트 설정 변경은 없었다.

사전 [12조건 탐색](../../artifacts/greedy-joint-budget/screening.json)은 과거 비용표의4링크조건×서버슬롯1/2/3 탐색이다. 이를 새 관측과 합치지 않았다. 새 계획은 슬롯2, 10/2Mbps·방향별20ms, 두 시간 블록, seed20261022, 각 고유 배치 워밍업2건·평가6건·0.1rps로 고정했다. 평가 입력은 evaluation split7–12이며, 보정20입력과 분리했다. 전체60입력은 품질 qualification에 사용했다.

보정 원본 `wan-20261020t045804`는9작업×노드 조합의60입력 qualification(워밍업 포함585건), 연산 p75와 전달 프로파일, 모든 계획 동결을 완료했지만 평가 시작 전 중단됐다. 최신 실행은 이 보정을 재사용했다. 마지막 보정 이벤트에서 재개까지 **19.18분** 경과했다. 런타임 소스7개와 bundle·원본 hash, 계획·입력·조건·시드 동일성을 확인했고 재개 시작 시점의 물리 자원 snapshot으로 선택 배치를 재검증했다. 각 Pod admission은 Kubernetes가 수행한다. 이번 재개를 새 보정이나 매 배포 직전의 자원 재관측으로 부르지 않는다.

동결 예측에서는10Mbps의 greedy/HEFT1172.41ms, 정확 열거968.54ms, 단순 교환982.44ms였다. 2Mbps에서는 모두 E0/C/C를 선택했지만 **이번 실측 대조는 실행하지 못했다**. 같은 host/cache와 동결 프로파일을 쓰는 두 블록 설계도 독립 환경 반복이 아니며, 현재는 첫 블록만 관측했다.

## 감사와 중단 경계

[부분 감사](../../artifacts/greedy-joint-budget/final-evidence/partial-verification-v1.json)는 전체 상태 `incomplete`, 완료 자료 상태 `passed`다. 전체 감사기를 임시 링크 뷰에서 재사용하며 동결 실행 순서와 실제 rows에서 관측 mapping/summary만 도출했다. 가짜 관측이나 완료 marker는 만들지 않았다. 원자료는 변경하지 않았다.

- 평가18/18, 워밍업 포함24/24 성공,72receipt. CPU reference Top5 순서 일치, 최대 score 절대 오차1.013278961e-6.
- 입력·중간·최종 hash, 실제 Pod UID/노드·CPU/thread, 비용표·정책·교환 재계산, 예산, 입력 분리, 배치 전 차단/relay 검사, 평가 후 drain·native backlog0을 확인했다.
- 누락된 조건, 완료 marker, 복구 검사 coverage의 세 항목은 실패로 그대로 보존했다. 마지막 `528-guard-False-vision` 명령의 event/stderr가 중단 지점을 특정한다. EOF 시 원격 변경의 부분 실행 여부는 미확인이므로 마지막 복구를 성공으로 간주하지 않는다.
- 별도 cleanup 확인은 소유 Pod/ConfigMap 잔여0·오류0이다. 정리 성공은 복구 확인 성공과 다른 사실이다.
- 보호 대상199개 중5개 Pod의 Ready가 True→False로 관측됐다. [종료 상태](../../artifacts/greedy-joint-budget/final-evidence/protected-after.json)에 보존했으며 실험과의 인과는 확인하지 않았다.
- [변조 검증](../../artifacts/greedy-joint-budget/partial-audit-negative-check.json)에서 출력 score를 바꾼 임시 복사본은 품질/hash 오류로 거부됐다. 로컬 runtime/budget 테스트15개 통과.

배포 준비 시간36.30–51.86초는 별도 보존했다. 위 요청 지연에는 배포·전체 보정·제어 실패 복구 비용이 포함되지 않는다. 정적 요청 지연 개선을 동적 전환 순이익으로 해석할 수 없다. 공개 파일을 엣지 Pod에서 재생한 입력이며 실제 센서 스트림 검증은 아니다.

## 이전 시도와 제어 경로

| 실행 | 보존한 결과 |
|---|---|
| `043924` | qualification 후 첫 전달 보정 전 worker exec EOF. 평가0건. |
| `045602` | 작은 소유 Pod의 exec 진단30/30 성공. 원인 재현 실패. |
| `045804` | 보정·계획 동결 완료, 첫 평가 준비 중 relay exec EOF. 평가0건. 보정 원본. |
| `051740` | 실제 모델·차단 경로에서 stdin 사용/미사용 각20회 exec 성공. stdin 원인설 아님. |
| `052248` | greedy/HEFT6/6, 평균1058.98ms 후 다음 배치 준비 중 EOF. 최신18건과 합산하지 않음. |
| `053023` | 동일 Pod netns의 netctl health 검사 진단 통과. |
| `053336` | 이 보고서의18/18 평가 후 netctl 복구 exec EOF. |

모든 위 시도의 cleanup은 잔여0·오류0으로 보존했다. 읽기 전용 health 검사에만 최대2회 EOF 재시도를 적용하고 각 실패 stdout/stderr/event를 남겼다. 마지막 실행의 health 검사는 실제 Pod UID를 확인한 같은 Pod network namespace의 netctl에서 수행했다. 이는 Pod 연결 검사이며 worker exec 신뢰성 검증이 아니다. 추론과 네트워크 변경 명령은 재시도하지 않았다. EOF가 netctl 변경 명령에서도 발생했으므로 관찰 컨테이너 변경을 근본 해결로 주장하지 않는다.

다음 과제는 KubeEdge exec EOF 원인 확인 또는 소유 Pod 경로 상태를 읽어 부분 실행을 조정하는 제어 절차의 별도 검증이다. 이후 동결 설계의 완주가 필요하다. 현재 자료만으로 두 번째 블록,2Mbps 대조,동적 관측/재배포 비용을 대체하지 않는다.

## 재현 자료

[간략 증거](../../artifacts/greedy-joint-budget/final-evidence/), [원자료 SHA256 목록](../../artifacts/greedy-joint-budget/raw-evidence-manifest.json), [검토 기록](../../artifacts/greedy-joint-budget/code-review.md)을 Git에 포함한다. 대용량 원자료와 모델 bundle은 로컬 `artifacts/topology-profile-live/`에 보존하며 Git에는 넣지 않는다. 다른 checkout에서 재감사하려면 보정 원본·평가 원자료·bundle을 별도로 전달해야 한다.

```bash
rtk proxy python3 experiments/real-topology-live/audit_partial_budget.py artifacts/topology-profile-live/wan-20261020t053336
```

감사 출력은 배타적으로 생성하므로 이미 결과가 있는 원자료에서 재실행하면 덮어쓰기를 거부한다. 재검증은 출력 파일을 제외한 별도 복사본에서 수행한다. 이 부분 감사 도구의 설명 범위는 이번 첫10Mbps 블록에 맞춰져 있다.
