# Live report — multi-edge sensor pilot (task_f8d9e0c8d4c9 / ctx_0fd08339d6bd)

- 실행 모델: 기존 Claude Code 구독 세션 `ee27a4b4-…`(claude-opus-5-5), 하위 agent·모델/공급자 변경 없음.
- Run `mes-live-20261022t101935z` (2026-10-22 10:19–10:41Z), namespace `topology-profile-lab`, native LAN, shaping 없음.
- **실제 클러스터 증거**(verify `offline: false`). 모델 변환 대조·로컬 smoke·fake kubectl 증거와 별개.

## 실행 전 고정

- `live-acceptance.json`의 24개 source hash 전부 일치. `prepared-20261022d`의 Pod runtime hash(runtime/models/rtsp/scenario/bootstrap) 일치 → 새 bundle 없이 사용.
- host 측 collector/plan/verify/reference와 재사용 helper(real-topology-live 전체 *.py, topology, real-topology-profile/prepare.py) 사본+SHA256SUMS:
  `artifacts/multi-edge-sensors/mes-live-20261022t101935z-host-sources/` (67 파일; 분석 스크립트 `live_summary.py` 포함).
- 읽기 전용 preflight: Ready9(dev0002/0003/0006/0008/0009, ser0001–0004), NotReady dev0001/0004/0007. 같은 namespace의 다른 작업 `diag-ec-fleet-*` Pod는 건드리지 않음.

## 명령 (모두 `rtk proxy`, cwd 저장소 루트, `C=/mnt/data3tb/multi-edge-sensors-20261022`, `P=$C/envs/pod311/bin/python`)

```bash
$P experiments/multi-edge-sensors/collect.py --execute --prepared $C/prepared-20261022d --run-id mes-live-20261022t101935z \
  > artifacts/multi-edge-sensors/mes-live-20261022t101935z.collect.stdout 2> ….collect.stderr      # exit 0
$P experiments/multi-edge-sensors/reference.py --run artifacts/multi-edge-sensors/mes-live-20261022t101935z --prepared $C/prepared-20261022d
$P experiments/multi-edge-sensors/verify.py --run artifacts/multi-edge-sensors/mes-live-20261022t101935z --prepared $C/prepared-20261022d \
  --output artifacts/multi-edge-sensors/mes-live-20261022t101935z.verify.json                          # exit 0
kubectl get pods,configmaps -A -l run-id=mes-live-20261022t101935z -o name | wc -l                       # 0
python3 artifacts/multi-edge-sensors/mes-live-20261022t101935z-host-sources/live_summary.py artifacts/multi-edge-sensors/mes-live-20261022t101935z \
  > artifacts/multi-edge-sensors/mes-live-20261022t101935z.summary.json
```

새 로컬 테스트·새 bundle은 만들지 않음(코드 변경 없음, 코드 수정 0건).

## 결과

**Source 증명**: 영상 3개(cam-e0@dev0003, cam-e6@dev0006, cam-e8@dev0008)의 MediaMTX·`runtime.py publish` 프로세스 alive, path ready, H.264 640×480 5 fps,
동결 프레임 loop 일관. 텍스트 2개(txt-e1@dev0002, txt-e9@dev0009) prepared 문장 hash 일치. 모든 평가 receipt의 source node/Pod UID가 해당 엣지 source와 일치(verify).

**후보 qualification (9 노드 × V/T/F, 보정 seq 0–2)**: 27 Pod 모두 Ready(dev0008/dev0009 image pull 포함), 보정 행 실패 0,
candidate-quality 제외 0 → 모든 workflow·task에서 9/9 후보. 즉 arm64(Pi5, Jetson, ser0003)와 x86 출력이 x86 CPU reference 허용치 이내.
보정 compute median(ms) — V(YOLO11n, 2 threads): Pi5 201–235, dev0009 289, ser0001 36, ser0002 40, ser0003 55, ser0004 70;
T(MiniLM): Pi5 25–27, dev0009 54, ser0001 14, ser0002 8.5, ser0003 6.8, ser0004 14.6; F <0.25.

**RTSP 프로토콜 probe (27 = 9 reader × 3 source, 배치 E2E와 분리)**: 27/27 ok, setup(FFmpeg probing 포함) 1558–1839 ms, 24 프레임 decode, skip 0.
**decode hash 불일치 집계**: 27 probe 중 18개(arm64 reader: Pi5 4대, Jetson, ser0003)는 동결 프레임 6개와 6/6 bit-identical, **9개(x86 reader ser0001/0002/0004 × 3 source)는 0/6 → 54개 frame 비교 불일치** — protocol status는 ok이고 각자 loop 일관이며 프레임 누락은 아님. 모든 원격 decoder가 bit-identical이라고 말할 수 없다.
reader 아키텍처와 일치하는 패턴이지만 원인은 증거로 확인하지 않았다. 배치 비교 입력(동결 PNG)에는 영향 없음.

**전송**: 동결 프레임(481,652 B PNG) pull — dev0003→타 노드 ≈44 ms, dev0006→≈5 ms, dev0008→≈6 ms(같은 노드 ≈1 ms).
응답 body bytes(w1): V→F 1,653(task 738), T→F 2,625(task 1,717), F→목적지 2,821(task 341 + receipt envelope 2,480). 189개 전송 probe 쌍 측정.

**계획**: 17개 고유 job(w1 5, w2 6, w3 6), 같은 workflow 안에서 배치가 겹친 정책 없음. w1 source-local은 capacity snapshot에서 dev0003 남은 CPU 2,215m < V 2,000m + F 250m라
`infeasible`로 기록·평가 안 함. kube-default(실제 default-scheduler 관측 후 re-pin)는 세 workflow 모두 V=ser0002, T=ser0004, F=ser0003.
exact는 V||T 병렬에서 T가 임계경로가 아니면 동률이 생겨 정렬 순서 첫 노드(dev0002)를 고른다(비용모형상 동률, 실측 우위 주장 아님).

**평가 (warmup 1 + 평가 3, seq 3–5; 68 attempt 모두 ok, 실패·누락 0; 품질 68/68 reference 허용치 이내)** — E2E = 목적지 source Pod에서 F `/run` 요청~응답(monotonic), 동결 프레임 경로이며 실시간 RTSP E2E가 아님.

| wf | 정책 | 배치 V / T / F | n | median | mean | SD | min–max | 예측 | Δmean vs task-fastest | Δmean vs kube-default |
|---|---|---|---|---|---|---|---|---|---|---|
| w1 | exact | ser0001 / dev0002 / dev0006 | 3 | 90.2 | 90.2 | 2.8 | 87.4–93.0 | 89.2 | −5.4% | −10.5% |
| w1 | greedy-network | ser0001 / ser0003 / dev0006 | 3 | 89.9 | 90.5 | 2.3 | 88.7–93.1 | 89.2 | −5.0% | −10.1% |
| w1 | task-fastest | ser0001 / ser0003 / ser0003 | 3 | 95.6 | 95.3 | 7.1 | 88.1–102.3 | 91.1 | 0 | −5.4% |
| w1 | server-fixed | ser0002 ×3 | 3 | 97.2 | 96.2 | 4.5 | 91.3–100.0 | 95.1 | +0.9% | −4.5% |
| w1 | kube-default | ser0002 / ser0004 / ser0003 | 3 | 100.2 | 100.8 | 3.4 | 97.6–104.4 | 97.0 | +5.7% | 0 |
| w1 | source-local | — | — | — | — | — | — | — | infeasible | |
| w2 | greedy-network | ser0001 / ser0003 / dev0006 | 3 | 53.7 | 53.2 | 2.2 | 50.7–55.1 | 50.3 | −2.2% | −10.7% |
| w2 | task-fastest | ser0001 / ser0003 / ser0003 | 3 | 55.5 | 54.4 | 1.9 | 52.1–55.5 | 52.8 | 0 | −8.7% |
| w2 | server-fixed | ser0002 ×3 | 3 | 53.3 | 55.6 | 4.6 | 52.7–60.9 | 52.1 | +2.3% | −6.6% |
| w2 | exact | ser0001 / dev0002 / dev0006 | 3 | 53.1 | 55.7 | 5.4 | 52.1–61.9 | 50.3 | +2.4% | −6.5% |
| w2 | kube-default | ser0002 / ser0004 / ser0003 | 3 | 57.4 | 59.5 | 5.1 | 55.9–65.3 | 53.3 | +9.5% | 0 |
| w2 | source-local | dev0006 / dev0009 / dev0006 | 3 | 224.0 | 225.0 | 3.1 | 222.5–228.5 | 215.2 | +313.9% | +277.9% |
| w3 | greedy-network | ser0001 / ser0003 / ser0001 | 3 | 52.8 | 53.5 | 2.2 | 51.8–55.9 | 50.3 | −5.4% | −7.7% |
| w3 | exact | ser0001 / dev0002 / ser0001 | 3 | 53.0 | 54.4 | 3.1 | 52.2–58.0 | 50.3 | −3.9% | −6.2% |
| w3 | task-fastest | ser0001 / ser0003 / ser0003 | 3 | 54.8 | 56.6 | 3.1 | 54.8–60.2 | 52.5 | 0 | −2.3% |
| w3 | kube-default | ser0002 / ser0004 / ser0003 | 3 | 56.7 | 58.0 | 2.8 | 56.1–61.2 | 56.0 | +2.4% | 0 |
| w3 | server-fixed | ser0002 ×3 | 3 | 54.8 | 58.3 | 6.3 | 54.5–65.6 | 55.5 | +3.0% | +0.6% |
| w3 | source-local | dev0008 / dev0002 / dev0008 | 3 | 214.7 | 215.8 | 3.1 | 213.5–219.4 | 215.0 | +281.4% | +272.4% |

단위 ms. 표의 Δ는 **평균 기준**: (mean_정책 − mean_기준)/mean_기준 × 100, 평가 행만. 기술 통계일 뿐 가설검정·유의성 주장 없음(n=3, 각 배치 1회 배포).
**중앙값 기준**((median_기준 − median_exact)/median_기준 × 100, exact의 감소율; coordinator 독립 재계산과 일치): exact vs task-fastest w1 5.62%, w2 4.27%, w3 3.28%;
exact vs kube-default w1 9.99%, w2 7.46%, w3 6.42%. 평균 기준으로는 w2에서 exact가 task-fastest보다 +2.4% 느리다(평가 1행 61.9 ms 영향) — 지표에 따라 방향이 바뀌는 수준의 차이다.

- 서버 배치 정책 간 차이는 대부분 수 ms(평균 2–10%)로 SD(2–7 ms)와 같은 규모다. 순위는 workflow마다 바뀌며 견고한 결론이 아니다.
- w1이 서버 배치에서 w2/w3보다 ≈35–40 ms 큰 것은 V 입력 pull(dev0003→≈41 ms, stage median)의 차이로 측정됨. 이 링크가 느린 원인은 진단하지 않았다.
- source-local(V를 Pi5 엣지에서 실행)은 YOLO ≈200 ms 때문에 ≈4배 느리다. 이 조건(native LAN, 동결 프레임)에서는 서버 실행이 지배적이다.

**선택·준비 비용(E2E와 별도)**: 결정 시간 task-fastest 0.03–0.06 ms, exact 3.2–4.5 ms(9³ 열거), greedy-network 4.5–13.1 ms.
단계 소요(s): sources 48.4, qualification V 234.7 / T 63.5 / F 90.1, transfer probes+/run 크기 109.1, scheduler 관측 17.9–21.0/workflow,
job별 배포+warmup+평가 20–89 s(엣지 Pod는 bootstrap 설치 때문에 더 김), 평가 전체 687, cleanup 6.6.

**정리**: 생성 106개 전부 UID 조건 삭제 성공(`deleted`), remaining 0, unresolved 0, preserved 0, `cleanup_verified=true`;
`kubectl get pods,configmaps -A -l run-id=…` 0개. 보호 Pod 252개 상태 변화 0(관측일 뿐 인과 주장 없음).
정상 UID 삭제만 실행됐으며 **실제 HTTP 409(UID 불일치) 경로는 이번 실행에서 발생하지 않아 live로 검증되지 않았다**.

## 증거 경로

- run: `artifacts/multi-edge-sensors/mes-live-20261022t101935z/` (design, phases, sources-evidence, frozen-inputs, calibration-reference, qualification,
  candidate-quality, transfer-probes, run-response-sizes, profiles/plans/schedule-frozen, scheduler-w*, attempts.jsonl, reference.json, cleanup, protected-diff, kubectl 호출별 event/stdout/stderr)
- `….collect.stdout/.stderr`, `….reference.stdout`, `….verify.json`, `….summary.json`, `…-host-sources/`

## 한계 / 남은 일

- 배치 비교는 동결 decoded PNG 입력이며 실시간 RTSP E2E·frame age 비교가 아니다. RTSP는 별도 프로토콜 probe로만 보고.
- 입력은 공개 정지 영상 3장의 결정적 변환·영어 템플릿 문장(합성 입력, 실제 센서 수집 아님); 검출 품질·데이터 다양성 주장 없음.
- n=3, 한 번의 배포·한 시간대, native LAN, 동시 부하 없음. 노드 수가 늘어난 효과나 DAG 공동 배치의 일반적 이점을 인과적으로 주장하지 않는다.
- x86 reader의 decode 불일치 원인, dev0003 링크 지연 원인 미진단.
- MediaMTX deprecated API 필드(`ready/tracks/bytesReceived`) 사용(v1.21.2에서 동작).
