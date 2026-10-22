# 기본 스케줄러와 프로파일 배치: 소규모 실측, 마지막 제어 실패

2026-10-21. Run `wan-20261021t101203`, 한국시간 19:12 시작. 기존 수집기 소스와 의존성 hash가 검토본과 같음을 확인한 뒤 동일 축소 설계로 새 보정부터 실행했다. 구현 변경은 없다.

**평가 9/9 성공, 전체 감사는 partial이다.** 마지막 평가 뒤 차단 해제의 exec 응답에서 unexpected EOF가 3회 반복됐다. 전체 수집 완료로 보고하지 않는다. 원자료와 실패·정리 기록은 변경하지 않았다.

## 관측

영상 ResNet18·음성 Wav2Vec2·결합 MUTAN CPU 워크플로. 영상 source는 dev0003, 음성 source는 dev0002의 파일 재생으로 고정. 서버는 ser0002. 물리 requests 기반 후보/용량을 사용하며 인위적 서버 슬롯 제한은 없다. 1블록, 10/2Mbps, 방향별 추가 지연20ms, 보정4입력·평가3입력, 저부하0.1rps. 같은 조건에서 같은 배치를 고른 정책은 하나의 관측을 공유한다.

배치 표기는 영상/음성/결합이며 C=서버, E0=영상 source 엣지다.

| 조건 | 정책 | 배치 | 평가 성공 | 평균 E2E |
|---|---|---|---:|---:|
| 10Mbps | kube-default, greedy, HEFT 변형, greedy 교환, 정확 열거, 네트워크 정보 제외 | C/C/C | 3/3 공유 | 718.39ms |
| 2Mbps | kube-default, 네트워크 정보 제외 | C/C/C | 3/3 공유 | 1,427.91ms |
| 2Mbps | greedy, HEFT 변형, greedy 교환, 정확 열거 | E0/C/C | 3/3 공유 | 934.51ms |

2Mbps의 두 배치 평균 차이는493.40ms, 감소율34.55%다. 이는 각3입력의 성공 지연 기술 통계이며 통계적 우월성·처리량 개선·전체 운영비용의 순이익이 아니다. 10Mbps는 같은 배치이므로 정책별 성능 차이를 만들지 않는다. 프로파일을 사용하는 단순 greedy도 정확 열거와 같아 새 알고리즘 고유 이득은 관측하지 못했다.

기본 스케줄러는 동일 후보에서 pin하지 않고 실제로 선택한 Pod/UID/이벤트를 기록했다. 표는 그 선택 위치를 다시 고정 배포한 성능이며 기본 스케줄링 전체 비용을 재현한 값이 아니다. HEFT는 serving 변형이며 원 논문 구현 재현이 아니다.

## 검증과 실패 경계

- 품질 검증108/108 통과. 본 평가9/9, 워밍업 포함12/12 성공. 워밍업 포함 stage receipt36개. 평가 최종 score 최대 절대 오차2.2352e-7.
- 저장된 감사기는 입력·모델·Pod identity·bytes/hash·품질·관측 그룹 등을 검사했다. 보고 상태는 `partial`, 오류는 실패 기록/완료 기록 없음/최종 policy map 없음/마지막 phase 실패/보호 workload 변화다. 이 감사를 pass로 재분류하지 않는다.
- `failure.json.partial_mapping`에 여섯 정책의 세 관측 그룹 연결이 보존돼 있다. `completed_labels`는 세 그룹 전체다. 최종 정상 경로의 `policy-observation-map.json`과 `pilot-summary.json`은 실패로 생성되지 않았다.
- 실패는 마지막 평가 이후 `remove_operators → guard(False) → sink-e0 unblock`의 제어 경로다. 3회 exec EOF 이후 중단됐으며 inference 재시도는 하지 않았다.
- finally 정리 결과 `remaining_owned=0`, `errors=[]`. 실패한 차단 해제의 원격 실행 여부와 원인은 이 사실만으로 판정하지 않는다.
- 보호 snapshot에서7개 변화가 있다: ebpf-budget-quick namespace3개와 edgeai-api Pod의 소실, jetson-gpu-exporter Ready 전환, edgemesh-agent 및 traefik NotReady 전환. 변화 원인·실험과의 인과를 확인하지 못했으며 공유 환경 교란 가능성이 있다.

## 시간과 비용 경계

품질 검증482.26초, 10/2Mbps 전달 보정11.37/14.75초, 실제 스케줄러 선택 phase22.46/22.41초. phase에는 해당 준비·제거 등 실행기 작업이 포함되므로 순수 알고리즘 시간으로 해석하지 않는다. 평가 phase에도 배포·warmup·drain·정리가 포함되며 E2E와 별개다. 위 표는 이 준비 비용을 상각한 전체 방식의 이익을 뜻하지 않는다.

## 이전 시도와 재현 자료

이전 `wan-20261021t094522`는 수집 프로세스가 사라지고 qualification 완료 기록이 없는 중단 실행이다. 평가 관측0이며 이번 결과와 합치지 않는다. 이전 run-id 잔여 자원은 별도 recovery 기록으로 정리했다(잔여0·오류0).

- [이번 원자료](../../artifacts/topology-profile-live/wan-20261021t101203/)
- [전체 감사 결과](../../artifacts/topology-profile-live/wan-20261021t101203/baseline-verification-v1.json)
- [실행·복구 기록](../../.scratch/scheduling-baseline-comparison/orchestration.md)
- [설계](scheduling-baseline-comparison-20261021.md)

실행 명령:

```bash
rtk proxy python3 -u experiments/real-topology-live/run_baseline_comparison.py --execute
rtk proxy python3 experiments/real-topology-live/verify_baseline_comparison.py artifacts/topology-profile-live/wan-20261021t101203
```

두 번째 명령은 현 보존 자료에서 종료코드1과 partial을 반환한다. 추가 실측은 반복하지 않았고, 제어 실패의 bounded 진단을 별도 Opus5.5 워커에 맡겼다.

## Opus 진단 후 확인

[별도 진단 보고서](../../.scratch/scheduling-baseline-comparison/live-diagnosis-report.md): cloudcore 로그에서 dev0003의 터널 단절(10:27:41.047860 UTC)과 재연결(10:27:51.407615 UTC)을 확인했다. 세 sink-e0 exec 요청은 이 공백 안에서 터널이 닫혔다는 오류로 거절됐다. 직접 실패는 제어 전송 경로이며, 왜 터널이 끊겼는지는 미확정이다. 저장된 응답을 기존 subprocess 경계에서 재생해 중단과 일시적 오류 복구의 차이를 확인했고 호스트도8개 검사를 재실행해 통과했다. 이는 터널 단절의 근본 원인을 재현한 것이 아니다.

추가 한계: 같은 단절 구간에서 dev0003 Pod의 worker/netctl 로그6개가 빈 stdout인데도 kubectl rc0으로 기록됐다. 따라서 cleanup errors=[]는 모든 진단 로그 확보를 뜻하지 않는다. 공식 감사의 partial 상태는 유지한다. 후속 원인 조사는 당시 edgecore journal 대조가 필요하며, 본 작업에서는 인프라 변경·retry 증설·추가 실측을 하지 않았다.
