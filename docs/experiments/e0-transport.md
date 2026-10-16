# E0 다음 단계: Task 분리와 실제 데이터 전달

관측일: 2026-10-16. 상태: 네 경로 전달·추론 검증 완료. EdgeCore 관측 결과도 아래에 별도 기록한다. 기존 [CPU 실행 검증](e0-kubeedge.md)을 같은 입력·모델의 분산 전달 경로로 확장한다.

## 고정한 범위

- T1 전처리·T3 후처리는 client Pod, T2 추론은 별도 worker Pod에서 실행한다.
- ARM edge와 x86 server에 worker를 하나씩 두고, 각 노드의 client가 두 worker의 고유 Service를 호출한다. 같은 노드도 서로 다른 Pod 사이의 HTTP 경로다.
- CPU EP·SqueezeNet1.1-7·입력 이미지와 전처리 계약은 이전 E0와 같다. 주요·전이 의존성은 이전 실제 설치 버전으로 모두 고정했다.
- 요청 body는 little-endian float32 NCHW `[1,3,224,224]`, 602,112 bytes. 응답 body는 float32 score 1,000개, 4,000 bytes다. HTTP/TCP header bytes는 여기에 포함하지 않는다.
- Task 간 데이터는 Service HTTP를 통과한다. 요청 ID·실제 worker node·모델 hash·입력/출력 hash·예상 top-5를 검사한다.

## 측정 설계

경로는 ARM→ARM, ARM→x86, x86→ARM, x86→x86이다. 각 경로 warmup 2회·측정 5회이며 client는 ARM, x86 순으로 한 번에 하나만 실행한다. 각 client 안에서는 worker 호출 순서를 번갈아 바꾼다. 동시 부하와 장기 성능의 검증은 아니다.

전처리, 직렬화/입력 hash, HTTP 응용 왕복, 서버의 body read·검증·추론·출력 encoding, 응답 검증/후처리와 전체 요청 시간을 구분한다. 모델 다운로드·설치·loading과 최초 health/DNS 검증은 요청 시간 밖이다. warm 모델에 대한 소규모 통신 파일럿으로 정의한다.

HTTP 응용 왕복은 DNS·연결·업로드·대기·서버 처리·응답 다운로드를 포함한다. 서버 추론시간을 빼더라도 순수 네트워크 지연이 되지 않는다. 서로 다른 노드의 시계를 빼 단방향 지연을 계산하지 않는다. HTTP 연결은 요청마다 새로 만든다.

## 실행 파일과 근거

- [실행 코드](../../experiments/e0/transport.py)
- [실행 정보·측정 조건](../../artifacts/e0-transport/run.json)
- [worker·Service·immutable ConfigMap manifest](../../artifacts/e0-transport/servers.json)
- [ARM client Job](../../artifacts/e0-transport/client-arm.json), [x86 client Job](../../artifacts/e0-transport/client-amd.json)
- [공식 자료 확인](../research/kubeedge-e0-transport-notes.md)

실제 사용한 명령:

```bash
rtk proxy kubectl apply --dry-run=client -f artifacts/e0-transport/servers.json
rtk proxy kubectl apply -f artifacts/e0-transport/servers.json
rtk proxy kubectl apply -f artifacts/e0-transport/client-arm.json
```

서버 준비 로그를 확인한 뒤 client를 실행했다. 기존 클러스터의 CNI·EdgeMesh·DNS·iptables·tc나 운영 workload 설정은 변경하지 않는다. 새 리소스는 scheduler-research namespace와 이번 run label로 한정한다.

## 해석의 한계

이는 데이터 경로·무결성·계측을 확인하는 pilot이다. 5개 요청을 독립 반복 실험으로 간주하지 않고 p95·통계적 우위·정책 개선을 주장하지 않는다. 같은 노드 배치와 분리 배치에서는 추론 노드의 연산 성능도 달라지므로 전체 지연 차이를 순수 네트워크 효과로 해석하지 않는다.

코드의 구문·실행 결과·요청/응답 증거를 확인한다. 이 폴더는 Git 저장소가 아니므로 commit 기준 diff를 요구하는 code-review 스킬의 정식 절차는 적용할 수 없으며, Git 기반 검토를 완료한 것으로 기록하지 않는다.

## 전달 검증 결과

총 28회 요청(워밍업 8·측정 20)의 client 기록과 server 수신 기록을 request ID로 일대일 대조했다. 602,112-byte 입력과 4,000-byte 출력의 byte 수·hash, 실제 worker node·모델 hash와 예상 top-5가 모두 통과했다. 두 client Job은 exit code 0으로 종료했다. 잘못된 길이와 잘못된 digest 요청도 두 worker에서 HTTP 400으로 거부했다.

아래는 경로별 측정 5회의 중앙값이다. 단위는 ms이며, 서로 다른 항목의 중앙값을 더해서 전체 시간으로 해석하지 않는다.

| T1/T3 위치 → T2 위치 | 배치 | 종단 | HTTP 응용 RTT | 서버 추론 |
|---|---|---:|---:|---:|
| ARM 엣지 → ARM 엣지 | 같은 노드·별도 Pod | 68.876 | 32.641 | 29.187 |
| ARM 엣지 → x86 서버 | 다른 노드 | 110.818 | 75.464 | 17.284 |
| x86 서버 → ARM 엣지 | 다른 노드 | 128.715 | 94.688 | 29.097 |
| x86 서버 → x86 서버 | 같은 노드·별도 Pod | 51.576 | 15.560 | 10.376 |

ARM에서 시작하는 고정 fixture에서 서버 추론 중앙값은 약 17ms로 ARM의 약 29ms보다 짧았지만, 종단 시간은 약 111ms로 ARM 내 배치 약 69ms보다 길었다. **Task 계산시간만 보고 실행 위치를 고르는 정책에 불리할 수 있는 최소 사례의 후보**다. 이는 알려진 계산/통신 절충의 관측이며 새로운 알고리즘의 필요성이나 기존 DAG 최적화의 실패를 입증하지 않는다.

동일 입력률의 독립 반복·부하/링크 통제·더 많은 입력을 추가해야 한다. 전송 경로를 packet trace로 추적하지 않았으므로 개별 요청이 EdgeMesh tunnel/CNI 중 어느 내부 경로를 탔는지는 확정하지 않는다. Service와 단일 endpoint·실제 처리 노드까지는 확인했다.

- [구조화 결과·min/max](../../artifacts/e0-transport/results.json)
- [28개 요청 기록](../../artifacts/e0-transport/requests.jsonl), [server 수신 기록](../../artifacts/e0-transport/server-receipts.jsonl)
- [ARM client 로그](../../artifacts/e0-transport/client-arm.log), [x86 client 로그](../../artifacts/e0-transport/client-amd.log)
- [ARM worker 로그](../../artifacts/e0-transport/server-arm.log), [x86 worker 로그](../../artifacts/e0-transport/server-amd.log)
- [Service·EndpointSlice 관측](../../artifacts/e0-transport/routing.json), [잘못된 payload 거부 검증](../../artifacts/e0-transport/negative-checks.json)

후속 x86 client 배포 명령도 실행했다.

```bash
rtk proxy kubectl apply -f artifacts/e0-transport/client-amd.json
```

## EdgeCore·CloudCore 직접 관측

사용자의 추가 요청에 따라 데이터 경로와 엣지 제어 경로를 함께 확인했다. 관리 SSH 경로로는 접근되지 않아, 동일 엣지 노드에서 별도 진단 Pod가 호스트 `/proc`, `/var/log`, `/run/log`를 **읽기 전용**으로 조회했다. journal 읽기에 필요한 UID 0을 사용했지만 privileged·hostPID·hostNetwork·서비스계정 token·추가 capability는 사용하지 않았다. 호스트 프로세스의 환경변수·명령행·키 파일은 읽지 않았다. EdgeCore 재시작이나 설정 변경은 하지 않았다.

- 관측 시각 `2026-10-16T06:34:40.124205+00:00`: EdgeCore 프로세스 1개, 상태 `S`, 연속 실행 약 27.54시간, RSS 약 113.4MiB. 이는 시점 관측이며 장기 CPU/메모리 profile이 아니다.
- journal에서 실제 실험 client Pod의 volume 준비 처리 로그를 확인했다. CloudCore는 Running/Ready이고 조회 당시 container restart count는 1이었다. 최근 CloudCore 로그 접근도 가능했다.
- 최근 15분을 대상으로 **최신 10,000개에서 상한에 도달**했으므로 전체 15분의 전수 로그가 아니다. 메시지 접두어 기준 경고 41개·오류 19개를 관측했다. journal PRIORITY는 모두 6이어서 이 필드만으로 오류 유무를 판단하지 않았다.
- 확인한 오류 표본은 `failed to process watch apps: map[]`와 flannel Pod의 serviceaccount metadata 조회 length error다. 경고 표본에는 watcher revision 강제 0 설정과 sysinfo CPU node 정보가 있다. 원인·영향은 아직 확정하지 않았고, 이번 28회 전달 성공으로 무시하지도 않는다.
- 이 오류가 관측 지연·재배치 적용·연결 복구에 영향을 주는지는 후속 조사 사항이다. 이번 파일럿에서 데이터 전달 지연의 원인이라고 단정하지 않는다. 연결 단절·복구 실험은 아직 수행하지 않았다.
- 첫 journal 읽기는 오래된 쪽부터 상한까지 읽혀 최신 로그가 누락될 수 있어 보존 후 역순 조회로 보완했다. 최종 결과는 최신 로그를 포함한다. EdgeCore 진단은 두 client 측정이 끝난 뒤 수행했다.

근거: [EdgeCore 최종 관측](../../artifacts/e0-transport/edgecore.json), [제어 경로 관측](../../artifacts/e0-transport/control-plane.json), [진단 코드](../../experiments/e0/edgecore_check.py), [읽기 전용 진단 Pod manifest](../../artifacts/e0-transport/edgecore-check-pod.json). journal API는 [systemd 공식 헤더](https://github.com/systemd/systemd/blob/main/src/systemd/sd-journal.h)를 확인했다.

## 다음 검증

- [x] 네 Service 경로의 실제 텐서 전달·추론·반환과 무결성 검증
- [x] 같은 노드의 별도 Pod 경로와 노드 간 경로를 구분한 파일럿 측정
- [x] EdgeCore 프로세스·실험 Pod 처리 journal 및 CloudCore 상태 관측
- [ ] EdgeCore watch/serviceaccount 오류의 범위와 제어 경로 영향 확인
- [x] 선택한 서버 GPU 1종·NPU 1종의 모델 실행·준비·fixture 오차 확인: [가속기 검증](e0-accelerators.md). 엣지 가속기·데이터셋 품질은 미검증
- [x] 같은 worker의 paced client 블록과 TCP 조건별 프로파일: [후속 576요청](e0-rpc-profile.md)
- [x] 세 Task의 정적 위치 선택 파일럿: [64배치와 새 평가](e1-dag.md). 측정 뒤 EdgeCore·100Mbps 협상값도 별도 관측
- [ ] 독립 배포 반복·통제한 링크 변화·네 필수 정책군 비교

읽기 전용 진단·worker·client·Service·ConfigMap은 증거 수집 후 이번 run의 정확한 이름으로 모두 삭제했다. [정리 증거](../../artifacts/e0-transport/cleanup.json)에서 잔여 리소스 0개를 확인했다. 기존 운영 workload와 namespace는 유지했다.
