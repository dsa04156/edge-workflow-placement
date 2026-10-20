# 보유 장비 자동 배치 파일럿

Run: `wan-20261020t024540`. 검증 상태: **passed**.

고정 엣지 이미지 E0·음성 E1에서 ResNet18 특징 추출, Wav2Vec2 ASR, MUTAN 합류를 실행했다. 각 Task는 품질 검증을 통과한 Pi 두 대와 CPU 서버 중에서 선택한다. 모델 선택·GPU·실제 센서 스트림 실험이 아니다.

단일 시간 block의 압축 입력 S1/S2 실행 파일럿이다. 같은 날짜/장비/host cache를 공유하며, 배치마다 새 Pod를 만들었어도 독립 환경 반복으로 해석하지 않는다. 보정20입력과 평가40입력은 분리했고, 이번 파일럿 평가는 고정된 첫6개 평가 입력을 사용했다. 정책별 같은 배치는 한 관측을 공유한다.

## 이번 관측의 결론

2Mbps에서 E0/C/C 배치의 평균917.44ms는 C/C/C의1271.25ms보다27.83% 낮았다. greedy·HEFT 서비스 변형·정확 열거(`ours`라는 코드 라벨)가 모두 같은 배치를 골랐다. 따라서 이 결과는 WAN 비용을 반영한 배치 변경의 효과이며, DAG 공동 최적화나 새 알고리즘만의 추가 이득을 입증하지 않는다. 무제한·100·10Mbps에서는 이 세 정책과 기본 스케줄러가 같은 C/C/C를 선택했다.

## 중단 시도와 복구

- `wan-20261020t015739`: 보정 완료 후 netctl의 `/config/net_control.py` 파일을 찾지 못해 평가 전 중단. 별도 25회 probe(`wan-20261020t021827`)에서는 재현되지 않아 원인은 미확정이다. 시작 시 코드 hash를 검증해 private 경로로 복사하고 netctl 준비 완료를 readiness로 확인하도록 보완했다.
- `wan-20261020t022614`: 보정 완료 후 기본 스케줄러 후보 명세의 다중 `metadata.name` 값이 API 검증에서 거부돼 평가 전 중단. 후보별 OR term으로 수정해 server dry-run 통과 후 전체 재실행했다.
- 위 보정·실패 자료는 최종 실행과 합치지 않았다. 모든 중단·진단·최종 실행은 소유 자원0개, 정리 오류 없음으로 종료했다.
- 최종 실행 수집기 commit은 `4ec1c90`; 동결 코드와 planner 원본 hash를 실행 디렉터리에 보존했다. 외부 Pod 상태 변화6건은 관측됐으나 인과는 미확인이다. 클러스터 전체가 변하지 않았다고 주장하지 않는다.

## 정책별 관측

값은 성공 요청의 평균 E2E ms다. 실패는 다음 표의 분모에 유지한다.

| 조건 | kube-default | server-only | greedy | heft | ours | no-network | no-node-profile |
|---|---:|---:|---:|---:|---:|---:|---:|
| unlimited | 220.11 | 220.11 | 220.11 | 220.11 | 220.11 | 220.11 | 1606.90 |
| compressed-100 | 580.89 | 580.89 | 580.89 | 580.89 | 580.89 | 580.89 | 1651.21 |
| compressed-10 | 668.97 | 668.97 | 668.97 | 668.97 | 668.97 | 668.97 | 1605.66 |
| compressed-2 | 1271.25 | 1271.25 | 917.44 | 917.44 | 917.44 | 1271.25 | 1679.60 |

## 실제 고유 배치와 성공률

배치 표기는 V/S/F 순서다. C는 보유 CPU 서버이며 외부 클라우드가 아니다.

| 조건 | 정책 | V/S/F | 실행 관측 | 성공/계획 |
|---|---|---|---|---|
| unlimited | no-node-profile | E0/E1/E0 | `eval-2-unlimited` | 6/6 |
| unlimited | kube-default, server-only, greedy, heft, ours, no-network | C/C/C | `eval-5-unlimited` | 6/6 |
| compressed-100 | kube-default, server-only, greedy, heft, ours, no-network | C/C/C | `eval-0-compressed-100` | 6/6 |
| compressed-100 | no-node-profile | E0/E1/E0 | `eval-4-compressed-100` | 6/6 |
| compressed-10 | no-node-profile | E0/E1/E0 | `eval-1-compressed-10` | 6/6 |
| compressed-10 | kube-default, server-only, greedy, heft, ours, no-network | C/C/C | `eval-6-compressed-10` | 6/6 |
| compressed-2 | greedy, heft, ours | E0/C/C | `eval-3-compressed-2` | 6/6 |
| compressed-2 | kube-default, server-only, no-network | C/C/C | `eval-7-compressed-2` | 6/6 |
| compressed-2 | no-node-profile | E0/E1/E0 | `eval-8-compressed-2` | 6/6 |

## 실행·검증

로컬 런타임 테스트12개와 기존 planner 테스트7개가 통과했다. 원자료를 바꾸지 않은 별도 사본에서 평균값 변조와 워밍업 누락을 주입해 감사가 두 오류를 검출하는 것도 확인했다.

- 평가 54/54 성공. 워밍업 포함 성공 72건, stage receipt 216건.
- 최종 출력의 CPU reference 대비 최대 score 절대 오차: 1.96695328e-06. Top5 순서·모델/입출력 hash·실제 Pod UID/노드·실제 serving thread1을 대조했다.
- 각 Task×노드에서 워밍업5 후60입력 품질 확인; 비용 계산은 calibration20만 사용했다. 세 Task는 순차로 프로파일링하며 비선택 모델을 평가 중 상주시키지 않았다.
- 실제 default-scheduler가 공통 후보 중 선택한 배치를 기록한 뒤 probe Pod를 제거했다. 그 선택과 다른 정책 선택을 같은 방식으로 새로 배포하고 고유 배치 실행 순서를 무작위화했다.
- 각 조건 마지막 두 원시 커널 backlog 관측, 전체 서비스 idle, 직접 경로 차단/복구와 소유 리소스 정리를 감사했다.

## 해석 경계

- 계획기는 p75 고립 연산시간과 실제 payload echo RPC 비용의 DAG critical path를 사용한다. echo는 직렬화·HTTP 왕복을 포함한 전달 대리 지표이며 실제 /execute·/join 프로토콜 전체나 순수 단방향 네트워크 시간이 아니다. 같은 노드 보정도 서로 다른 Pod 사이로 전달했다.
- /open은 작은 echo 하나, 최종 결과는 역방향 payload echo 하나로 근사한다. 공유 링크 동시 전송·연산 간섭·큐·관측/배포 비용은 예측 목적함수에 포함되지 않는다. 예측값을 실제 total_ms 최적해로 부르지 않는다.
- HEFT는 upward rank와 earliest-finish 선택을 독립 상주 서비스 실행 의미에 맞춘 변형이다. 원 논문의 단일 처리기 lane 실행을 재현했다는 주장이 아니다. 정확 열거도 새 알고리즘이 아니다.
- 공유 WAN은 실험 소유 gateway Pod의 방향별 상한과 지연이다. TCP가 중계에서 분리되며,100Mbps는 상한 설정이지 실효100Mbps 달성 주장이 아니다.
- 기존 MUTAN vocab 재구성의 한계는 유지한다. CPU reference 일치는 원 질문의 정답 정확도나 센서 인식 정확도를 뜻하지 않는다.
- 이 파일럿은 통계적 우월성·장기 SLO·새 알고리즘 필요성의 증거가 아니다. 동일 배치 정책 간 수치 중복은 동일 관측을 공유한 결과다.
- native 직접 경로 대조, raw 별도 보정/sweep, 독립 block 반복·전수 배치 확인, source 위치 S3, Jetson S4와 부하 S5는 후속 단계다.

## 원자료와 재실행

원자료: `artifacts/topology-profile-live/wan-20261020t024540`. 코드와 배포/프로파일/선택/실패/정리 자료를 로컬에 보존했다. Git에는 [감사·계획 요약](../../artifacts/owned-hardware-pilot/final-evidence/)의 감사·계획·요약과 [원자료 hash 목록](../../artifacts/owned-hardware-pilot/raw-evidence-manifest.json)의 원자료23,939개 파일 hash를 기록한다. 원자료 디렉터리와 대용량 모델 bundle은 이 결과 커밋에 포함하지 않으므로 다른 checkout에서 재감사하려면 별도로 전달해야 한다.

```bash
rtk proxy python3 -u experiments/real-topology-live/run_placement.py
rtk proxy python3 experiments/real-topology-live/verify_placement.py artifacts/topology-profile-live/wan-20261020t024540
rtk proxy python3 experiments/real-topology-live/report_placement.py artifacts/topology-profile-live/wan-20261020t024540 --out docs/experiments/owned-hardware-placement-pilot.md
```

첫 명령은 준비된 대용량 로컬 bundle이 필요하며 실험 리소스를 생성한다. 검증/보고서는 기존 출력이 있으면 덮어쓰지 않는다.

보호 리소스 종료 관측:
```json
{
  "protected_count": 199,
  "changes": [
    {
      "uid": "8c33b895-df66-42b3-8201-b458e97b0e2c",
      "before": {
        "name": "edgeai-serial-json-5d70b0f0619adeda-5ffb59457f-k8g8x",
        "namespace": "edgeai-devices",
        "phase": "Running",
        "ready": "False"
      },
      "after": null
    },
    {
      "uid": "29da800c-1344-4e40-beb3-74c5ed3167d9",
      "before": {
        "name": "jetson-gpu-exporter-hgf2t",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "False"
      },
      "after": {
        "name": "jetson-gpu-exporter-hgf2t",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "True"
      }
    },
    {
      "uid": "166f2772-2a0f-4380-aaf0-556b397fa099",
      "before": {
        "name": "kube-flannel-edge-ds-jgh7d",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "False"
      },
      "after": {
        "name": "kube-flannel-edge-ds-jgh7d",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "True"
      }
    },
    {
      "uid": "342e95a2-fecc-4024-8b17-c1a4f4dec4d8",
      "before": {
        "name": "node-hardware-inventory-nqg4l",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "False"
      },
      "after": {
        "name": "node-hardware-inventory-nqg4l",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "True"
      }
    },
    {
      "uid": "81bdffc9-b91f-4baf-9c06-633ff2fb8e09",
      "before": {
        "name": "nvidia-jetson-device-plugin-nano-gtl48",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "False"
      },
      "after": {
        "name": "nvidia-jetson-device-plugin-nano-gtl48",
        "namespace": "kube-system",
        "phase": "Running",
        "ready": "True"
      }
    },
    {
      "uid": "ffedfea1-266e-4687-ac8f-e552414a68bc",
      "before": {
        "name": "edgemesh-agent-4x8ld",
        "namespace": "kubeedge",
        "phase": "Running",
        "ready": "False"
      },
      "after": {
        "name": "edgemesh-agent-4x8ld",
        "namespace": "kubeedge",
        "phase": "Running",
        "ready": "True"
      }
    }
  ]
}
```
