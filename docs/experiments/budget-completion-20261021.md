# 서버 슬롯 비교 완주: 두 블록·10/2Mbps 완료

2026-10-21. Run `wan-20261021t044624`, HEAD `43f9d11`, 실행기 `run_budget_placement.py`
(동결 설계 그대로, 새 보정부터). E0=dev0003, E1=dev0002, C=ser0002 CPU. NotReady
노드는 원래 후보가 아니며 사용하지 않았다. 이전 부분 실행(`wan-20261020t082011` 등)과
합산하지 않는다.

**두 블록 모두 완료, `verify_placement.py` 감사 통과(errors=[]).**

## 환경 전제

센서가 엣지 장비에 붙어 있는 엣지 컴퓨팅 환경을 가정한다. 이미지 입력은 E0, 음성 입력은
E1 Pi에서만 발생하도록 source를 엣지에 고정했고, 서버 C로 보내려면 제한된 WAN
(10/2Mbps+20ms)을 거쳐야 한다. 실제 센서 스트림이 아니라 엣지 Pod의 공개 이미지·음성
파일 재생이다.

## 관측 결과

이미지 source E0·음성 source E1, ResNet18/Wav2Vec2/MUTAN CPU, 서버 작업 슬롯2개,
방향별20ms 추가 지연. 배치당 평가6건의 성공 평균. 같은 배치를 고른 정책은 관측을 공유한다.

| 조건 | greedy / HEFT 변형 | 정확 열거 / greedy 한 번 교환 |
|---|---:|---:|
| b0 10Mbps | 1,124.54ms (C/C/E0) | 890.53ms (E0/C/C) |
| b1 10Mbps | 1,111.07ms (C/C/E0) | 885.65ms (E0/C/C) |
| b0 2Mbps | 893.70ms (E0/C/C, 4정책 동일) | 같음 |
| b1 2Mbps | 911.62ms (E0/C/C, 4정책 동일) | 같음 |

배치 표기는 영상/음성/결합 순서다.

- 10Mbps 정확 열거의 greedy 대비 감소: b0 234.01ms/20.81%, b1 225.42ms/20.29%,
  두 블록 기술 평균 1,117.80 → 888.09ms(229.71ms/20.55%).
- 동결 예측: greedy 1,247.4ms, 정확 열거·교환 957.0ms, 2Mbps 전 정책 953.0ms.
- **이번 보정에서는 greedy 한 번 교환이 정확 열거와 같은 배치를 선택했다.** 이전
  시도(교환 E1/C/C, 잔여 121.93ms)와 달리 단순 보완만으로 차이가 전부 사라졌다.
  따라서 정확 열거나 새 알고리즘의 고유 이득을 뒷받침하지 않는다.
- 2Mbps는 계획대로 일치 대조군이다.

## 품질·감사

- 새 9작업×노드 품질 검증 585/585 통과.
- 평가 36/36, 워밍업 포함 48/48 성공, stage receipt 144, 최대 score 절대 오차 1.013e-6.
- 전체 감사 `placement-verification-v1.json`: status=passed, errors=[].
- cleanup remaining_owned=0, errors=[]. 비0 returncode 이벤트 0. 제어 exec EOF 재발 없음.
- 평가 입력은 evaluation split 7–12 재사용이다. 새 holdout, 독립 환경 반복이 아니며
  유의성 검정·CI를 제시하지 않는다.

## 교란 가능성

실행 중 보호 대상 Pod 변화 6건은 모두 다른 연구의 eBPF namespace
(`ebpf-clock-check-20261021`, `ebpf-shared-repeat-v3-20261021-0`)가 스스로 삭제한 것이다.
이 eBPF 실험은 C 노드인 ser0002에도 Pod를 두며(hostNetwork 포함) 공유 qdisc 혼잡을
만든다. 실행 시간과 겹쳤으므로 서버 측 지연에 영향을 줬을 수 있다. 원인 분리는 하지 않았다.

## 한계

정적 배치 메커니즘 pilot이다. 서버 슬롯 제한은 연구용 예산이며 물리 포화가 아니다.
2026-10-21 사용자 결정으로 동적 재배치(네트워크 전환 관측 지연·재배포 비용·순이익)는
논문 범위에서 제외한다. 이 결과는 정적 배치 결과로만 서술한다.
5정책(교환 후 한 작업 이동) 실측과 GPU 노드 확장은 후속이다.

## 재현

```bash
rtk proxy python3 -u experiments/real-topology-live/run_budget_placement.py
rtk proxy python3 experiments/real-topology-live/verify_placement.py artifacts/topology-profile-live/wan-20261021t044624
```
