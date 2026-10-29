# 센서 작업의 처리 성능·네트워크 인지형 서버 선택

## 실측 결과 — 완료

2026-10-29 22:37:41–22:45:24 KST, `gmp-sensor-20261029t133741`. **고유 조건·서버별 1회씩 총 4건을 실행했고, 모두 성공·경로 검증·5초 진단 SLO를 통과했다.** 소유 실험 Pod 5개와 ConfigMap 1개 정리 후 API로 부재를 확인했다.

| 네트워크 조건 | GPU 서버 실제 E2E | NPU 서버 실제 E2E | 처리만 고려 | 처리+네트워크 고려 |
|---|---:|---:|---|---|
| 정상 경로 | 2.444초 | 0.456초 | NPU | **NPU** |
| NPU 목적지 2Mbps 제한 | 2.416초 | 3.888초 | NPU | **GPU** |


정상 경로에서는 처리 성능+네트워크 정책이 NPU를 골라 GPU 고정 대비 관측 완료시간이 **81.35%** 낮았다. NPU 목적지 전송을 제한하자 같은 정책은 GPU로 선택을 바꾸었고, 처리 성능만 보고 NPU를 고른 경우 대비 **37.85%** 낮았다. 이는 각 1회 관측의 차이다. 새 알고리즘 우월성이나 실제 Kubernetes 기본 스케줄러 대비 개선으로 일반화하지 않는다.

| 조건 | 후보 | 서버 처리시간 추정 (ms) | source 전송 probe (ms) | 예상 합계 (ms) | 실제 E2E (ms) |
|---|---|---:|---:|---:|---:|
| 정상 | GPU | 2425.840 | 23.025 | 2448.865 | 2444.413 |
| 정상 | NPU | 439.422 | 18.895 | 458.317 | 455.793 |
| NPU 저속 | GPU | 2425.625 | 28.165 | 2453.790 | 2416.279 |
| NPU 저속 | NPU | 439.536 | 3444.784 | 3884.320 | 3887.946 |

원본 이미지 NPY는 818,048 bytes다. NPU source 전달 probe는 정상 18.895ms에서 제한 조건 3444.784ms로 증가했다. GPU 경로 probe는 각각 23.025ms/28.165ms였다. 설정한 2Mbps와 별도로, 실제 probe bytes/RTT로 계산한 NPU 제한 경로의 응용 전달속도는 약 1.900Mbps였다. HTTP/relay overhead를 포함하므로 물리 링크 용량은 아니다.

서버 처리시간 추정에는 전처리+VLM+후처리 실측과 같은 서버 내부 HTTP probe가 포함된다. 최종 반환은 작은 응답 근사이며, 중첩 RPC 시간을 추가로 합산하지 않는다. E2E는 엣지 source에서 예약 시각부터 최종 응답까지의 monotonic 시간이다. 사전 모델 준비·정책 선택은 이 warm 요청 시간 밖이며 선택 시간은 원시 계획에 따로 남겼다.

결정과 계획은 본 평가 전에 동결했다. 정상 조건에서는 처리만/처리+네트워크/NPU고정이 N 관측을 공유하고, 제한 조건에서는 처리+네트워크/GPU고정이 G 관측을 공유한다. 네 정책을 네 개의 독립 표본으로 세지 않는다. 모든 본 응답 문자열은 `no`였지만 정답 품질 검증은 아니다. 조건별 관측에 근거한 서버 선택 실증이며 연속 온라인 재최적화·재배치 실험은 아니다.

## 목적과 결정 단위

센서 데이터 처리 작업 전체를 GPU 또는 Mobilint NPU 서버 한 곳에 배치한다. **후보별 실제 처리 성능과 source→후보의 실측 전송 비용을 함께 보고 예상 완료시간이 짧은 서버를 선택**한다. 전처리·추론·후처리를 서버 사이에 나누는 DAG 배치가 아니다.

기존 chain 실행기는 선택된 한 서버 안에서만 재사용한다. 허용 배치는 GGG와 NNN뿐이며 혼합 배치는 거부한다. G는 server3 NVIDIA GB10, N은 server4 Mobilint ARIES, source는 dev0003 Raspberry Pi다. CPU 전용 추론 후보와 dev0009는 제외한다. 전처리·포맷팅 및 런타임의 CPU 보조 처리는 남는다.

## 짧은 비교 계약

| 정책 | 선택 근거 |
|---|---|
| GPU 우선 고정 기준선 | 실행 가능한 GPU 서버를 고정 선택 |
| NPU 고정 참조 | 실행 가능한 NPU 서버를 고정 선택 |
| 처리 성능만 고려 | 실측 서버 처리시간 최소 |
| 처리 성능+네트워크 | 실측 서버 처리시간 + source 전송 probe 시간 최소 |

고정 기준선은 실제 kube-scheduler 실행 결과가 아니다. GPU/NPU 자원 이름이 다른 실제 스케줄러의 노드 선택까지 이 실험으로 검증했다고 주장하지 않는다.

- 정상 전달 경로: source→GPU/NPU 모두 동일한 실험용 gateway를 경유하며 제한 없음.
- NPU 전송 저속 조건: 같은 경로에서 NPU Pod 목적지에만 2Mbps를 적용. 실험 소유 gateway Pod 네임스페이스만 변경하며 호스트 네트워크와 다른 작업은 변경하지 않는다.
- 결정 입력은 명목 2Mbps가 아니라 실제 전달 probe 관측값이다. probe는 실제 입력과 크기가 같은 합성 bytes이며 HTTP/relay 처리까지 포함한다. 본 요청은 동결된 실제 이미지와 질문을 사용한다.
- 동일 입력 `cal-3733440`, 조건별 두 서버를 각 1회 실행하여 본 측정 총 4회. 같은 서버를 선택한 정책은 관측을 공유한다. 사전 워밍업·프로파일·경로 점검은 별도로 기록한다.
- 사전 비용표와 선택을 동결하고 실제 E2E를 별도로 측정한다. 비용에 따라 선택이 바뀌지 않거나 이득이 없으면 그대로 보고한다.

## 해석 범위

GPU는 Qwen3-VL-2B BF16, NPU는 같은 계열 W4V8 MXQ다. 보유 모델·휠을 사용하고 외부 모델 다운로드는 없다. 정밀도/품질 동등성은 평가하지 않는다. 고정 이미지의 엣지 재생이며 실제 센서 스트림 수집은 아니다. Warm 단일 작업 비교로, 큐 포화·cold 배포·p95·통계적 우월성·품질 조건부 goodput·물리 WAN topology를 주장하지 않는다. 이전 단계분할 실험은 별도 예비 자료로 보존한다.

## 실행 근거와 재현

- [간결 요약](summary.json), [요청 CSV](analysis/requests.csv), [정책 CSV](analysis/policies.csv), [단계 CSV](analysis/stages.csv), [동결 계획](evidence/plans.json)
- [전체 원시 기록](raw-run.tar.gz): phase 시작/종료, stdout/stderr, profile/probe/qualification, main ledger, qdisc·gateway 증거와 cleanup 포함. CSV는 Git 표시를 위해 줄바꿈만 LF로 정규화했다.
- [실행 소스 archive](source-snapshot.tar.gz), [소스 hash manifest](source-snapshot-manifest.json), [승인 release](evidence/approved-r2.json), [정확한 실행 명령·종료코드](evidence/live-exit.json)
- [지침·명세](evidence/spec.md), [구현 보고](evidence/developer-report.md), [수정 보고](evidence/fix-report-r1.md), [최종 요구 검토](evidence/review-spec-r2.md), [최종 구현 검토](evidence/review-standards-r2.md)
- 기존 65개 테스트 및 변경 구간 25개 테스트 통과 기록을 보존했다. 두 수는 중복 테스트를 포함하므로 90개 독립 테스트라고 합산하지 않는다. [원 테스트 증거](evidence/test-evidence.json), [보완 테스트 증거](evidence/fix-test-evidence.json)
- [정리 증거](evidence/cleanup.json), [정리 후 부재](evidence/absence-after-cleanup.json). 사용자가 지정한 기존 상주 서비스 파드만 사전 종료했으며 [승인 작업 기록](evidence/prior-service-removal.json)을 남겼다.

첫 실행 명령은 기존 실행기의 run-id 접두사 규칙에 맞지 않아 클러스터 자원 생성 전에 거부됐다. 그 로그도 보존했으며, 접두사만 바로잡은 본 실행은 exit 0이다. 이 인자 오류를 추론 실패나 평가 표본으로 세지 않는다.

재분석은 source archive를 `.scratch/sensor-server-placement-20261029/`에 풀고 raw archive를 해제한 뒤 `python3 -B staged/experiments/gpu-mobilint-placement/chain_analyze.py <run-directory> <new-output-directory>`로 수행한다. 새 실장비 실행에는 release에 바인딩된 로컬 모델·휠·입력 및 클러스터 접근이 필요하다. 모델 파일은 Git에 포함하지 않는다.
