# wan-20261021t101203 최종 control EOF 진단 (bounded, read-only)

- 작성: 2026-10-21, Orca task `task_d19f8ac5982b` / dispatch `ctx_369781a5c9bc`
- 대상: `artifacts/topology-profile-live/wan-20261021t101203` (원본 artifact 무수정, 실험 재실행 없음)
- 모델/인증 경로: Claude Code 세션 `2b11ae05-acdf-4e1b-9e1b-8788f96101b9`, 모델 `claude-opus-5-5` (coordinator 측 transcript에서 확인됨). 셸 환경에서 `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_MODEL`은 모두 unset입니다. 따라서 API 키나 다른 provider로 넘어간(fallback) 흔적은 관측되지 않았습니다.
- 지침 이탈 기록: kubectl 호출은 모두 `rtk proxy`를 거쳤습니다. 하지만 초반 일부 보조 명령(`which`, `ls`, `grep`, `python3`, `date`, `stat`, `wc`, `sed`, `orca` 등)은 rtk 없이 실행했습니다. 모두 읽기 전용 명령이었고, 쓰기는 이 보고서와 `live-diagnosis/` 아래 파일로만 한정했습니다.

## 결론 (요약)

1. **직접 실패 경계**: 2026-10-21 10:27:42.361Z–10:27:44.606Z에 `remove_operators('eval-2-compressed-2')` → `guard(False)` → `net('sink-e0', unblock)` → `execute_control` 경로에서 kubectl exec가 3회 연속 `error sending request: Post "https://10.254.164.138:10369/exec/..." : unexpected EOF`로 끝났습니다(events 974/975/976, 각 rc=1, 64–115 ms). 이후 retry 한도(3회)를 넘겨 `RuntimeError('976-guard-False-sink-e0-receipt-2 failed; inspect phase stderr')`가 발생했습니다. 이 오류의 기록 시각은 `phase-013-evaluation-eval-2-compressed-2.end.json`의 `finished_at=10:27:44.606178Z`입니다. `failure.json` 자체에는 시각 필드가 없으므로, 파일 mtime은 이벤트 시각의 근거로 쓰지 않았습니다.
2. **인접 원인(관측됨)**: sink-e0가 있던 엣지 노드 `etri-dev0003-raspi5`(10.254.164.138)의 KubeEdge cloudcore 터널 세션이 **10:27:41.047860Z에 `websocket: close 1006 (abnormal closure): unexpected EOF`로 끊겼습니다**. 노드는 **10:27:51.407615Z에 재연결**됐습니다(공백 약 10.36 s). 세 exec 시도는 모두 이 공백 안에 들어가며, cloudcore가 각 시도를 `the tunnel connection of Tunnel session [etri-dev0003-raspi5] has closed`로 **엣지로 전달하기 전에 거절**했습니다.
3. **판정**: 증거는 이번 실패의 **인접 원인이 전송 계층(KubeEdge cloud↔edge 터널) 실패**라는 쪽을 뒷받침합니다. 이번 진단 범위에서 실험 코드의 기능 결함은 **확인되지 않았습니다**. 다만 이것이 아직 알려지지 않은 결함이 없다는 뜻은 아닙니다. 코드는 설계대로 retry 3회(sleep 1 s ×2, 약 2.2 s)를 시도한 뒤 중단했습니다. 다만 retry 창(약 2.2 s)이 실제 터널 공백(약 10.4 s)보다 짧았다는 **창 길이 불일치**는 사실로 확인됐습니다. 이것을 "retry 횟수를 늘리면 된다"는 처방으로 읽지 않습니다(아래 "남은 미확정" 참고).
4. **터널 단절의 근본 원인은 미확정**입니다. 엣지 측 edgecore 로그나 노드 네트워크는 이번 범위(읽기 전용 kubectl)에서 볼 수 없었습니다. 따라서 엣지 프로세스 재시작, 네트워크 단절, KubeEdge exec 정리 경로 버그 가운데 어느 것인지 단정하지 않습니다.
5. **완료된 측정(평가 요청 9/9 성공)은 "부분 진단 결과"로만 보고할 수 있습니다.** 이 run은 `completed.json`, `policy-observation-map.json`, `pilot-summary.json`이 없고 보호 워크로드 변화 7건이 있어 공식 비교 결과가 아닙니다.

## 1. 정확한 오류 경계와 시각

| 시각 (UTC) | 출처 | 내용 |
|---|---|---|
| 10:27:40.112–41.078 | event 971 | `guard-False-source-e0` exec (dev0003) **성공**, rc 0, stdout 정상 JSON |
| 10:27:40.149799 | cloudcore | `APIServer_ExecConnection MessageID 468` dev0003 세션 추가 (= 971) |
| 10:27:41.035207–41.0479 | cloudcore | MessageID 468에서 `failed send REMOVE_CONNECT message to edge, err: <nil>` / `max retry count reached` 반복 **5,344줄** (약 13 ms 동안) |
| **10:27:41.047860** | cloudcore | `get Tunnel session [etri-dev0003-raspi5] reader error websocket: close 1006 (abnormal closure): unexpected EOF` → `edge node etri-dev0003-raspi5 ... disConnected` |
| 10:27:41.077–41.994 | events 972, 973 | source-e1(dev0002), source-c(ser0002) unblock 성공 |
| 10:27:42.361–42.477 | event 974 | sink-e0 1차 시도 → EOF. cloudcore 10:27:42.447047: `add apiServer exec connection into Tunnel session [etri-dev0003-raspi5] error the tunnel connection ... has closed` |
| 10:27:43.478–43.542 | event 975 (`-receipt-1`) | 2차 시도 → EOF. cloudcore 10:27:43.511580 같은 거절 |
| 10:27:44.542–44.606 | event 976 (`-receipt-2`) | 3차 시도 → EOF. cloudcore 10:27:44.587127 같은 거절 → `RuntimeError` |
| 10:27:44.606 | phase-013 end | `evaluation-eval-2-compressed-2` status=error, duration 101,976 ms |
| 10:27:44.8–45.4 | cloudcore / events 980,981,986,987,992,993 | dev0003 Pod(source-e0, sink-e0, vision) `kubectl logs`가 cloudcore에서 `Failed to get container logs ... has closed`. **그런데 kubectl은 rc 0과 빈 stdout(0 byte)을 반환**했습니다(아래 "증거 손실" 참고). |
| 10:27:45.8–58.6 | events 999–1001 | `close()` cleanup: delete → wait → remaining 0 |
| **10:27:51.407615** | cloudcore | `edge node etri-dev0003-raspi5 ... connected` (재연결) |
| 10:28:25.9 이후 | cloudcore | dev0003 exec 세션이 다시 정상 생성됨(다른 주체의 호출) |

참고: cloudcore 로그 시각은 KST(+9)로 찍혀 있습니다(`I1021 19:27:41` = 10:27:41Z). 세 거절 시각은 각 kubectl 시도 구간 안에 정확히 들어갑니다.

### 해석상 핵심 포인트

- `10.254.164.138:10369`는 dev0003의 `daemonEndpoints.kubeletEndpoint.Port=10369`입니다. kube-apiserver의 exec 요청이 cloudcore stream 서버를 거쳐 dev0003 터널로 가는 경로입니다. "error sending request ... unexpected EOF"는 apiserver가 이 hop에서 응답 헤더를 받기 전에 연결이 닫힌 경우입니다.
- cloudcore가 "add apiServer exec connection ... has closed" 단계에서 거절했으므로, **sink-e0 unblock control(operation `3f19bcffa59a407d96aed617406d49e8`)은 엣지로 전달되지 않았습니다.** at-most-once receipt도 Pod 안에 생성되지 않았을 것으로 봅니다(Pod가 이미 삭제되어 직접 확인은 불가).
- 같은 run에서 dev0003 대상 exec는 10:12–10:27에 수십 회(분당 1–11회) 정상이었습니다. guard(False) 자체도 이전 세 평가 group 사이에 sink-e0/source-e0에서 각각 성공했습니다(552/712/843 등). containerexec `REMOVE_CONNECT` 실패 폭주는 10:12 이후 cloudcore 로그에서 **이 1건(dev0003, 19:27:41)에만** 나타납니다. 19:26 근처에 보이는 REMOVE_CONNECT 문자열은 dev0007의 다른 메시지(`can not find apiServer connection id`)였습니다.
- 같은 시간대에 dev0001(jetorn)과 dev0007(jetagx)은 약 30–70 s 주기로 connect/disConnect를 반복했습니다. 이 run과 무관한 클러스터 배경 불안정 신호입니다. dev0003은 09:56:26Z(cloudcore 재시작 이후) 연결 뒤 10:27:41Z까지 단절 기록이 없습니다.

## 2. 오프라인 replay (red-capable)

- 스크립트: `.scratch/scheduling-baseline-comparison/live-diagnosis/replay_final_guard_eof.py`
- seam: `Session.call` 안의 `subprocess.run`입니다. 기존 `test_budget_placement`와 `test_control_receipt`가 patch하는 지점과 같습니다. `uuid.uuid4`는 캡처된 operation ID로 고정했습니다. 그래서 생성된 kubectl argv가 **events 971–976의 캡처 argv와 바이트 단위로 같은지** 단언합니다. `time.sleep`은 기록만 하고 실제로 대기하지 않습니다. 클러스터, 네트워크, artifact에는 쓰지 않고 출력은 temp dir로만 보냅니다.
- 시나리오 A(캡처 재현): source-e0/e1/c는 캡처된 stdout, sink-e0는 캡처된 EOF stderr를 3회 모두 반환합니다.
- 시나리오 B(대조군): sink-e0 EOF를 1회만 냅니다. retry 분기가 살아 있음을 보여 줍니다.
- 실행 명령:

```bash
cd /home/jinuk/codex-work/scheduler
rtk proxy python3 .scratch/scheduling-baseline-comparison/live-diagnosis/replay_final_guard_eof.py
```

- 출력(요약, 전체는 `live-diagnosis/replay-output.txt`), exit 0:

```
A_captured: error="976-guard-False-sink-e0-receipt-2 failed; inspect phase stderr", sink_attempts=3,
            sleeps=[1,1], guarded=true, exec_keys=[source-e0,source-e1,source-c,sink-e0,sink-e0,sink-e0],
            logs_or_delete=[], stderr_files=971..976 (캡처와 동일한 파일명), counter=976
B_transient: error=null, sink_attempts=2, guarded=false (sink-e1..fusion까지 진행)
PASS A aborts with captured error
PASS A three sink-e0 attempts
PASS A two 1s sleeps (~2s retry window)
PASS A exec order matches 971-976
PASS A no logs/delete in remove_operators
PASS A guard state left True
PASS A counter ends at 976
PASS B transient EOF recovers
```

- red 확인: `scenario(2)`(EOF 2회 뒤 3번째 시도 성공)는 `error=None, guarded=False`가 됩니다. 이 경우 "A aborts with captured error" 단언은 FAIL이며, 판별력이 있음을 확인했습니다.
- **replay가 증명하는 것**: 캡처된 3연속 EOF 응답만 주어지면, 현재 코드는 캡처와 같은 tag와 순서, 같은 예외로 중단합니다. 이 중단은 logs/delete와 `refresh_network`를 건너뛰고 `guarded=True`를 남깁니다. 그 뒤 `collect()`의 `finally: session.close()`가 cleanup을 맡습니다.
- **replay가 증명하지 않는 것**: dev0003 터널이 왜 끊겼는가. 이것은 인프라 측 근본 원인이며 재현하지 못했습니다.

## 3. 코드 결함 vs 전송 실패 판정

- 판정 범위: 인접 원인은 전송 실패로 보이며, 기능 결함은 확인되지 않았습니다. 알려지지 않은 결함의 가능성은 배제하지 않습니다.
- 전송 실패를 뒷받침하는 증거: cloudcore 로그에 노드 단위 터널 단절과 재연결 시각이 있고, 같은 시각 같은 노드의 logs 요청도 실패했습니다. 다른 노드(dev0002, ser0002) exec는 같은 구간에 성공했습니다. 실패한 요청은 엣지에 도달하지 않았습니다.
- 코드 동작: 설계대로입니다. transport EOF는 같은 operation으로 최대 3회 retry하고, 그 밖의 오류는 즉시 raise하며, 불확정 control은 replay하지 않습니다.
- 관찰된 코드 측 약점(결함 단정은 아님, 수정은 구현하지 않음):
  1. retry 창 약 2.2 s가 이번 터널 공백 약 10.4 s보다 짧았습니다. 하지만 공백 길이 분포는 표본 1개뿐이어서 retry를 늘린다고 일반적으로 해결된다고 말할 수 없습니다. 단절 시점에 따라 엣지 측 상태는 다를 수 있습니다.
  2. `close()`와 `remove_operators()`의 `kubectl logs`는 터널이 닫혀 있어도 **rc 0과 빈 stdout**을 받습니다. 그래서 오류가 아니라 정상 수집으로 기록됩니다. 이번 run에서 dev0003 Pod 3개(source-e0, sink-e0, vision)의 worker/netctl 로그 6개가 0 byte로 남았지만 `cleanup.json errors=[]`입니다. 이전 run `wan-20261021t044624`의 e0 로그는 비어 있지 않았습니다(예: source-e0 worker 239,328 B). 이것은 증거 손실이며, 검증기에도 드러나지 않습니다.

## 4. 측정 결과 (부분 진단 결과로만 보고 가능)

각 group은 같은 3개 evaluation 질문(3506790, 5341940, 787710), warmup 1, 20 ms 지연, compressed 표현입니다. 1 block, n=3이므로 CI, 유의성, 우열 주장은 불가합니다.

| group | 조건 | 해당 정책 | 실제 배치 (vision/speech/fusion) | 성공/시도 | 평균 e2e (ms) | 개별 (ms) |
|---|---|---|---|---|---|---|
| eval-0-compressed-10 | 10 Mbps | kube-default, greedy, heft, greedy-swap, exact, no-network (6개 모두 같은 배치) | ser0002 / ser0002 / ser0002 | 3/3 | **718.39** | 698.4, 678.7, 778.1 |
| eval-1-compressed-2 | 2 Mbps | kube-default, no-network | ser0002 / ser0002 / ser0002 | 3/3 | **1427.91** | 1383.0, 1437.6, 1463.1 |
| eval-2-compressed-2 | 2 Mbps | greedy, heft, greedy-swap, exact | **dev0003** / ser0002 / ser0002 | 3/3 | **934.51** | 923.7, 928.8, 951.0 |

- 품질과 receipt: 9개 행 모두 `status=ok`, `quality_ok=True`, `answer_id_matches=True`, `top5_order_matches=True`, `deadline_exceeded=False`, `scope=real_ai_file_replay`, `split=evaluation`입니다. 각 행에 vision/speech/fusion receipt 3개가 있습니다. `max_score_abs_error`는 eval-0과 eval-1의 6개 행에서 모두 `0.0`입니다. eval-2(vision이 dev0003에 배치됨)는 0이 아니며, 질문 순서대로 `2.2351741790771484e-07`, `1.7881393432617188e-07`, `1.564621925354004e-07`입니다(최대 2.2351741790771484e-07). 이 오차가 있어도 answer_id와 top5 순서는 일치했고 quality_ok=True였습니다. 따라서 "9개 모두 오차 0"은 사실이 아닙니다. coordinator가 실행한 `baseline-verification-v1.json`의 결과는 `status=partial`, groups 3, evaluation 9/9 성공, 실패 0, receipts 36(평가 27 + warm 9)이며, identity/hash 오류는 없습니다. partial 사유는 failure.json 존재, completed.json 부재, policy-observation-map.json 부재, phase-013 실패, "protected workloads unchanged" 미충족입니다.
- eval-2 측정은 drain(960–967, 10:27:39.2Z~), qdisc, snapshot, relay-counters(968–970, 10:27:40.11Z 종료)까지 수집된 뒤 끝났습니다. 실패는 측정 행 저장과 drain이 끝난 **뒤** 정리 단계의 guard 해제에서 났고, 측정 행에서 직접적인 오류는 관측되지 않았습니다. 그렇다고 영향이 없다고 단정하지는 않습니다. 같은 시간대에 공유 클러스터에서 dev0001/dev0007 터널 flap, 외부 롤아웃, 다른 실험 네임스페이스 삭제가 있었으므로 환경 교란(confounding)의 가능성은 남아 있습니다.
- 주의: 2 Mbps에서 엣지 vision 배치가 전부 서버 배치보다 짧게 관측됐습니다. 이것은 n=3, 1 block, 순서 고정의 단일 관측입니다. 정책 우열 근거로 쓰지 않습니다.

## 5. cleanup과 보호 워크로드

- `cleanup.json`: `remaining_owned=0`, `errors=[]`입니다. 소유 Pod 11개(cache, gateway, source×3, sink×3, vision, speech, fusion)와 ConfigMap 1개가 delete/wait로 사라졌습니다(events 999–1001, 10:27:45.8–58.6Z). 현재 `topology-profile-lab` 네임스페이스에 Pod가 없습니다(10:30Z 확인). 삭제 명령의 범위는 `-n topology-profile-lab`과 `run-id=wan-20261021t101203` 라벨, 그리고 run이 소유한 resource list였습니다.
- 남은 영향: sink-e0의 guard block은 해제되지 않았지만, Pod 삭제로 해당 netns도 함께 사라졌습니다(sink-e1, sink-c, vision, speech, fusion도 마찬가지입니다). `restored-final` shape 해제와 `remove_operators`의 per-label logs/delete도 실행되지 않았고, 대신 `close()`가 수행했습니다.
- `protected-after.json`: protected 211개 가운데 **7개가 변경**됐습니다. 이 run의 delete 범위 밖이며, 원인은 아래처럼 **증거 수준을 구분해** 적습니다.

| Pod | 변화 | 읽기 전용 확인 결과 | 이 run과의 관계 |
|---|---|---|---|
| ebpf-budget-quick-20261021-0/backend-a, backend-b, router | 사라짐 | 해당 네임스페이스가 현재 NotFound | 다른 실험 네임스페이스. 누가 삭제했는지는 미확인. run 삭제 범위에 없음 |
| edgeai/edgeai-api-6648865b77-5g9p8 | 사라짐 | ReplicaSet `767575f5b7`(28분 전)과 `5fc8947dbc`(16분 전)가 서로 다른 이미지 digest로 새로 생성됨 → Deployment 롤아웃 | 외부 배포로 보임(주체 미확인) |
| kube-system/jetson-gpu-exporter-bb4n2 (dev0007) | ready False→True | 현재 1/1 | dev0007 터널 flap 구간 |
| kubeedge/edgemesh-agent-jn6bq (dev0007) | ready True→False | 현재 1/1 | dev0007 터널 flap 구간 |
| traefik/traefik-5f9db57887-jc9km (**dev0003**) | ready True→False | 현재 1/1 | snapshot(약 10:27:58Z)이 dev0003 재연결(10:27:51Z) 직후 → 같은 노드 단절의 여파로 보임(인과 미증명) |

## 6. 남은 미확정

- dev0003 cloud↔edge 터널이 10:27:41Z에 끊긴 근본 원인. 엣지 edgecore 로그(journal), 노드 네트워크와 전원 상태, edgecore 재시작 여부는 이번 범위에서 볼 수 없었습니다. source-e0 exec(MessageID 468)가 끝나는 시점과 단절이 13 ms 안에서 겹치지만, 같은 노드에서 수십 회의 exec가 문제없이 끝났으므로 exec가 단절을 일으켰다고 증명할 수 없습니다.
- sink-e0 at-most-once receipt가 Pod 안에 없었다는 점의 직접 증거. cloudcore 거절 단계로 미루어 추정할 뿐이며, Pod는 삭제됐습니다.
- 보호 워크로드 4개 삭제의 주체. 외부 롤아웃과 네임스페이스 삭제로 보이지만 감사 로그로 확인하지 않았습니다.

## 7. 최소 다음 행동 (구현하지 않음)

1. (근본 원인, 다음 진단 후보) dev0003에서 `journalctl -u edgecore --since "2026-10-21 10:27:30 UTC" --until "2026-10-21 10:28:00 UTC"`(읽기 전용)를 확인할 수 있습니다. 읽기 전용 조회이므로 기존 사용자 권한 범위 안에서 가능한 진단이며, 별도 승인이 반드시 필요한 단계로 보지 않습니다. 엣지 측 단절 사유(재시작, write timeout, 네트워크)를 보고 인프라 원인을 확정하거나 기각합니다.
2. (보고) 이번 run을 "평가 9/9 성공, 정리 단계 transport 중단, partial"인 **부분 진단 결과**로만 문서화합니다. 위 표의 수치를 정책 비교 결론으로 쓰지 않습니다.
3. (설계 검토 대상, 코드 변경 아님) 빈 stdout logs를 "수집 성공"으로 처리하는 증거 손실 문제와, 터널 단절 시 guard 해제 단계의 처리 방식은 Astra/coordinator의 설계 판단 대상입니다. 단순 retry 증설은 권고하지 않습니다.

## 진단 evidence 파일

- `.scratch/scheduling-baseline-comparison/live-diagnosis/replay_final_guard_eof.py`: 오프라인 replay
- `.scratch/scheduling-baseline-comparison/live-diagnosis/replay-output.txt`: replay 출력(+ red 확인)
- `.scratch/scheduling-baseline-comparison/live-diagnosis/cloudcore-stream-102739-102810.log`: cloudcore 10:27:39–10:28:10Z stream 관련 줄(upstream/metrics 제외, 비밀 키워드 필터)
- `.scratch/scheduling-baseline-comparison/live-diagnosis/cloudcore-session-events-since-0956.log`: 09:56Z 이후 노드 connect/disConnect 및 터널 오류 줄
