# 서버 슬롯 비교 완주 시도: 새 보정·첫 블록 완료, 제어 연결 중단

2026-10-20. Run `wan-20261020t082011`, collector `45fce51`.
**전체 실험 미완료. 완료된 첫 블록의 관측 자료 감사는 통과했다.**
첫 블록의10/2Mbps 평가24건 뒤 복구 확인 exec EOF가 연속3회 발생했다.
두 번째 블록은 실행하지 않았고, 마지막 복구 확인을 성공으로 간주하지 않는다.

## 관측 결과

이미지 source E0·음성 source E1, ResNet18/Wav2Vec2/MUTAN CPU 실행,
워크플로 전용 서버 작업 슬롯2개, 방향별20ms 추가 지연이다. 물리 서버
포화나 실제 경쟁 작업을 재현한 조건은 아니다.

| 첫 블록 조건 | greedy / HEFT 변형 | 정확 열거 | greedy 한 번 교환 |
|---|---:|---:|---:|
| 10Mbps | 1,099.28ms | 872.51ms | 994.45ms |
| 2Mbps | 872.59ms | 872.59ms | 872.59ms |

각 고유 배치당 평가6건의 성공 평균이며 실패는0이다. 같은 배치를 고른
정책은 관측을 공유한다. 10Mbps의 영상/음성/결합 배치는 각각
C/C/E1, E0/C/C, E1/C/C였고,2Mbps에서는 모두 E0/C/C였다.

- 10Mbps 정확 열거의 greedy 대비 감소: **226.76ms /20.63%**.
- 단순 교환의 감소: **104.83ms /9.54%**. 관측된 차이의46.23%를 줄였다.
- 단순 교환과 정확 열거의 잔여 차이: **121.93ms**.
- 이전 부분 실행의16.80%와 방향은 같지만, 별도 보정·별도 시도의 관측이다.
  두 시도를 계획된 두 블록으로 합치거나 독립 표본으로 집계하지 않는다.

## 논문 주장에 중요한 단순 보완 대조

[사후 비용표 진단](../../artifacts/budget-completion/fresh-single-move.json)에서
한 번 교환한 배치를 기준으로 작업 하나의 실행 노드를 바꾸는 후보를
검사했다. 두 대역폭의 동결 비용표 모두에서 정확 열거와 같은 배치를 찾았다.
10Mbps에서는 영상 작업을 E1에서 E0로 옮기며,2Mbps에서는 추가 변경이 없다.
4개 조건 entry는 두 비용표를 계획된 두 블록에 복제한 것으로,4회 독립
확인이 아니다. [이전 비용표에서도 같은 결과](../../artifacts/budget-completion/previous-partial-single-move-v2.json)였다.

이는 **교환 후 최대 한 작업 이동이라는 단순 보완으로 배치 차이를 없앨 수
있다는 근거**다. 해당 보완을 사전 등록된 새 실측 정책으로 실행한 것은
아니며 선택시간·온라인 전환 비용도 측정하지 않았다. 현재 validator/
planner/policy 소스가 동결 원본과 같은지 확인하고 분석기와 입력 hash를 남겼다.
첫 진단 파일은 보존했고 구현 provenance를 추가한v2를 기준으로 사용한다.

따라서 이 자료는 자원 제약에서 단계별 greedy의 배치 손실을 보여주는
실측 파일럿으로 사용할 수 있지만, 복잡한 새 알고리즘 필요성이나 정확
열거만의 고유 이득을 뒷받침하지 않는다.

## 품질·설계·감사

- 새9작업×노드 품질 검증585건 모두 통과. 모델 준비5회 워밍업 뒤60입력을
  검사했고, 비용에는20개 calibration 입력만 사용했다.
- 평가24/24, 워밍업 포함32/32성공,96stage receipt. Top5 순서 일치,
  최대 score 절대 오차1.013278961e-6.
- [부분 감사](../../artifacts/budget-completion/partial-verification-v1.json)는
  입력·중간·출력 hash, 실제 Pod UID/노드/thread, 정책/교환 재계산,
  입력 분리, 배치 예산, 종료 ledger·drain을 대조했다.
- 전체 감사의 실패5항목을 그대로 유지했다: 전체 조건 coverage,
  완료 marker, 복구 coverage, restore-sink-e1의 재시도 한도와 최종 성공 증거.
  기대하지 않은 관측 감사 오류는0이었다.
- 별도 사본에서 첫 출력 score에0.5를 더한
  [변조 검증](../../artifacts/budget-completion/negative-audit.json)은
  최종 payload hash와 수치 품질 오류로 거부됐다. 원자료 hash는 보존됐다.
- 평가 입력은 이전 부분 실행에서도 사용한 evaluation split7–12다.
  새 holdout 입력·다른 날짜·독립 환경 반복으로 부르지 않는다. 같은
  host/cache의 단일 시간 블록 기술 통계이며 유의성 검정이나 CI를 제시하지 않는다.
- 요청 지연은 전체 보정·새 배포·제어 장애 복구 비용을 포함한 동적 순이익이 아니다.

## 중단·복구 진단

[제어 응답 유실 복구 검증](budget-control-recovery.md) 후 새 전체 수집을
시작했다. 이번 수집에서 네트워크 변경 명령은 EOF 없이 완료됐다.
하지만 `2579/2580/2581-restore-sink-e1`의 읽기 전용 exec가 모두
`error sending request: ... unexpected EOF`로 끝났다. 이후 소유 자원은
0개·정리 오류0이었지만, 정리 성공은 직접 연결 복구 증거와 다르다.
보호 대상 Pod 상태 변화10건이 기록됐으며 실험과의 인과는 미확인이다.

후속 진단 `wan-20261020t085217`은 같은 Pod worker의 차단·복구 확인2건을
통과했지만, 다음 cycle의 `guard-True-sink-e1` 네트워크 제어가 연속EOF로
중단됐다. 예정된 worker 확인6건과 전체 진단을 통과하지 못했다.
이 진단도 소유 자원0개·정리 오류0으로 종료됐다.

읽기 전용 worker fallback 및 이전 평가를 제외한 명시적 calibration
reuse 후보는 로컬 회귀를 통과했으나, 실환경 진단 전체가 실패했으므로
**실행 기본 코드에서 제거하고**
[미검증 후보 patch](../../artifacts/budget-completion/unvalidated-worker-fallback.patch)에
보존했다. 기본 수집기는 검증된 `45fce51`로 유지한다. 이를 EOF의 근본
해결로 부르지 않는다. 현재 추가 수집은 중단됐고 남은 실험 Pod/ConfigMap은0이다.

다음 실행의 선행 조건은 Pi E1의 exec 제어 연결 원인 확인 또는 실환경에서
검증된 대체 제어 경로다. 그 뒤 원 설계 두 블록을 완주하고, 단순 보완
정책을 별도로 사전 고정해 비교해야 한다. 이번24건을 남은 블록으로
대체하거나 유리한 결과만 골라 합치지 않는다.

## 재현과 보존

```bash
rtk proxy python3 experiments/real-topology-live/audit_completion_attempt.py artifacts/topology-profile-live/wan-20261020t082011 --out NEW_AUDIT.json
rtk proxy python3 experiments/real-topology-live/analyze_single_move.py artifacts/topology-profile-live/wan-20261020t082011 --out NEW_DIAGNOSTIC.json
```

출력은 배타적으로 생성한다. 원자료와 대용량 모델 bundle은 로컬
`artifacts/topology-profile-live/`에 있고, Git에는 요약·동결 계획·원자료
hash manifest를 보존한다. 다른 checkout의 재감사에는 원자료 전달이 필요하다.
Semantica 도구 호출이 불가능해 이번 기록은 로컬 문서에 남겼다.
