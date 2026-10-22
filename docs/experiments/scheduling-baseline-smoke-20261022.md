# 기본 스케줄러·프로파일 배치 비교: 재실행의 부분 결과

2026-10-22. run `wan-20261022t024224`. **정책 성능 비교는 미완료**. 변경 없는 축소 수집기로 한 번 재실행했다. 02:42:24–02:52:48 UTC, 종료코드 1. 추가 반복은 수행하지 않았다.

## 확보한 측정

ResNet18 영상, Wav2Vec2 음성, MUTAN 결합의 3작업×3노드에 대해 품질 검사 108/108 통과. 각 조합은 warmup5+보정4+평가3. 아래는 보정4개의 연산시간 p75(ms)이며 전송·준비 비용을 제외한다. 워크플로 E2E나 정책 성능 값이 아니다.

| 작업 | 엣지 E0 | 엣지 E1 | 서버 C |
|---|---:|---:|---:|
| 영상 | 684.84 | 698.55 | 122.32 |
| 음성 | 1679.21 | 2052.83 | 235.26 |
| 결합 | 751.42 | 1014.83 | 27.68 |

E0=etri-dev0003-raspi5, E1=etri-dev0002-raspi5, C=etri-ser0002-cgnmsb. 연산만 보면 서버가 빠르지만 전송 보정과 배치 평가를 하지 못했으므로 전체 서버 배치가 최선이라고 결론 내릴 수 없다.

## 실패 지점과 근거

qualification 완료 후 `session.guard(True)`의 sink-e1 제어에서 kubectl exec 연결이 `unexpected EOF`로 실패했다. 최초 명령과 동일 operation/Pod identity를 사용하는 receipt 경로 2회까지 총3개 stderr에서 확인된다. 에러는 `263-guard-True-sink-e1-receipt-2 failed`이다. 연결 단절의 근본 원인은 미확정이며 알고리즘 실패나 모델 실패로 해석하지 않는다. 과거 실행은 fusion 준비 대기에서 기록이 끊겼고 원인 기록이 없었다. 이번에는 해당 지점을 통과했다.

- 전달 비용 보정: 미완료.
- 정책 선택·배치 E2E 평가: 0회.
- 감사 결과: partial, 성능 비교 근거 없음.
- 자동 cleanup: remaining_owned=0, errors=[]. 종료 후 run label의 Pod/ConfigMap 부재를 재확인.
- 보호 workload 206개 중4개의 Ready 상태가 전후 달랐다. 인과관계는 확인되지 않았으며 보호 workload 불변 검사는 통과하지 못했다. 별도 workload를 변경하지 않았다.

기존 원본 자료와 이번 자료는 합산하지 않았다. source provenance의 이전 run 대비 해시 변경은 없었다. 구현 수정·새 의존성·push·merge는 수행하지 않았다.

## 실행 및 증거

- 실행: `rtk proxy python3 -u experiments/real-topology-live/run_baseline_comparison.py --execute`
- 감사: `rtk proxy python3 experiments/real-topology-live/verify_baseline_comparison.py artifacts/topology-profile-live/wan-20261022t024224 --output .scratch/scheduling-baseline-comparison/retry-20261022/audit.json` → exit1, partial.
- [원본 자료](../../artifacts/topology-profile-live/wan-20261022t024224/)
- [실패 traceback](../../artifacts/topology-profile-live/wan-20261022t024224/failure.json)
- [연산 보정값](../../artifacts/topology-profile-live/wan-20261022t024224/compute-p75.json)
- [감사 결과](../../.scratch/scheduling-baseline-comparison/retry-20261022/audit.json)

다음 기술 작업은 전체 모델 보정의 반복보다 sink-e1의 kubectl exec 제어 연결을 최소 재현으로 확인하는 것이다. 이번 기록만으로 연결 계층의 수정안을 확정하지 않는다.
