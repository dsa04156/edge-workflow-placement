# 다중 엣지 가상 RTSP·텍스트 입력 + YOLO11n/MiniLM DAG 배치 (2026-10-22)

## 상태 (먼저 읽기)

| 레벨 | 상태 |
|---|---|
| 코드 존재 | `experiments/multi-edge-sensors/` (prepare / collect / reference / verify / smoke / fake kubectl) |
| 관련 테스트 통과 | 빠른 테스트 30개 + `test_execute`(fake kubectl 전체 경로, `offline-test_execute-r2b`) — 검토 반영 절 참조 |
| 로컬 실제 프로토콜·모델 smoke | 통과(x86_64 한 호스트) |
| 실제 클러스터 실행 | **완료** — run `mes-live-20261022t101935z`, Ready9 후보·엣지 source 5개, 68 attempt 전부 ok, verify passed(`offline: false`), cleanup 0 |
| 배치 성능 결론 | n=3 기술 통계만(아래 실측 절). 유의성·일반화 주장 없음 |

fake kubectl 실행의 수치는 모두 로컬 오프라인이며 클러스터·엣지 성능 근거가 아니다(`design.environment=local-fake-kubectl`, `verify.py` 출력 `offline: true`).

## 설계 (명세 결정 반영)

- **source 매핑(엣지 고정)**: 영상 `cam-e0`@dev0003, `cam-e6`@dev0006, `cam-e8`@dev0008; 텍스트 `txt-e1`@dev0002, `txt-e9`@dev0009. workflow w1=(cam-e0,txt-e1), w2=(cam-e6,txt-e9), w3=(cam-e8,txt-e1); 결과 목적지는 영상 source 엣지. 서버 source는 `scenario.validate`가 거부(엣지 role만 허용).
- **가상 입력(실제 센서 수집 아님)**: 공개 정지 영상 3장(ultralytics `bus.jpg`, `zidane.jpg`, COCO val2017 `000000039769.jpg`)을 seed 기반 crop/zoom/밝기 변환해 640×480 프레임 12장 loop를 만들고, 좌상단 192×16 영역에 seq 바코드(동기 2셀+10비트)를 넣는다. 텍스트는 seed 템플릿 영어 이벤트 문장.
- **RTSP**: 각 영상 source Pod 안에서 MediaMTX v1.21.2(공식 release, checksums 일치)와 PyAV 18.1.0(번들 FFmpeg/libx264) publisher 프로세스. RTSP/TCP, H.264 yuv420p, 5 fps, GOP=loop=12, bf=0, qp 23, threads 1. MediaMTX는 RTSP와 Pod 내부 API 외 서버(RTMP/HLS/WebRTC/SRT/MoQ) 비활성.
- **입력 동결과 분리**: source Pod가 자기 RTSP 스트림을 2 loop 이상 decode해 바코드로 seq를 식별하고, seq별 decoded RGB를 PNG(무손실)로 동결한다. 배치 비교의 V 입력은 이 동결 PNG(source→V HTTP pull)다. 라이브 RTSP는 각 V 후보 노드→각 영상 source의 별도 **프로토콜 probe**(setup/demux 대기/CPU decode/프레임 hash)로 보고하며 배치 E2E와 합치지 않는다.
- **모델**: V=YOLO11n(`yolo11n.pt` v8.3.0 → ONNX opset17 640×640, CPU ORT 1.23.2, conf 0.25/IoU 0.7), T=all-MiniLM-L6-v2 공식 `onnx/model.onnx`(revision `1110a243…`) + tokenizer.json, mean pooling+L2, 80개 클래스 prompt cosine. F=`fused_c = max_det_score_c × max(0, cos_c)`, alert ≥0.2 — 학습 모델 아님.
- **DAG**: source(video)→V, source(text)→T, V∥T→F→목적지. 비용모형은 기존 `real-topology-profile/prepare.cost`(무큐 임계경로, 병렬 경로는 max).
- **정책**: kube-default(실제 default-scheduler 관측 후 re-pin), task-fastest(`plan.taskwise_fastest`: V,T,F 순서로 남은 자원에 맞는 quality-valid 후보 중 측정 실행비용 최소, 네트워크·임계경로 무시, 둘 곳이 없으면 unavailable — review r2에서 joint `profile_only` 오용을 교체), greedy-network(`placement_policies.sequential`), exact(`prepare.plan topology_profile`, 기존 비용모형의 열거 기준해, 새 알고리즘 아님), 진단 source-local / server-fixed. 같은 workflow 안 같은 배치는 하나의 job·관측을 공유.
- **절차**: preflight → cache(서버) → controller(서버) → source 5 Pod → task별 9 후보 qualification(보정 seq 0–2만, warmup 1) + RTSP probe → F Pod 간 payload pull probe(전 쌍, 같은 노드 포함) + 목적지 probe → workflow별 profile·계획 동결 → default-scheduler 관측 → seed 순서 job → job마다 V/T/F Pod 배포, warmup1+평가3(seq 3–5) → 정리. 전체 평가 workflow 상한 72(설계 최대 3×6×3=54).
- **제어 경로**: `kubectl exec`는 서버 노드의 소유 cache(bundle 1회 업로드, `exec -i`)와 controller Pod에만. 엣지 Pod는 controller가 HTTP(`/batch`, `/drive`)로 구동 — 구형 EdgeCore exec stdin race 회피. Pod init은 cache에서 hash 고정 bundle을 HTTP로 받아 `pip --no-index --no-deps --require-hashes`로 emptyDir에 설치.
- **시간**: 모든 지속시간은 한 호스트 monotonic. E2E는 목적지 source Pod에서 F 요청~응답. cross-host timestamp 차감 없음.
- **Pod 자원(request=limit)**: V 2 CPU/1536Mi(2 threads), T 1/1Gi, F 250m/384Mi, 영상 source 1/768Mi, 텍스트 source 100m/128Mi, cache 500m/512Mi, controller 200m/256Mi. capacity snapshot에 맞지 않는 노드는 apply하지 않고 탈락 사유 기록. runAsNonRoot 65532, readOnlyRootFilesystem, drop ALL, SA token 없음, hostNetwork/hostPath/privileged 없음.
- **품질 허용치(사전 고정, `models.TOLERANCE`)**: 클래스 순서 동일, box ≤1 px, score ≤1e-3, embedding cosine ≥0.9999, similarity ≤1e-4, fusion 결정 동일·점수 ≤1e-3. 기준은 같은 ONNX bytes의 로컬 CPU reference(`reference.py`). 위반 행은 제외하지 않고 `verify.py` findings로 보존.

## 원본 대비 모델 검증 (로컬, 실행 완료)

- YOLO export(`export-20261022c`, `yolo11n.onnx` sha256 `8ba5e065…dca2`, 원본 `yolo11n.pt` `0ebbc80d…4ee1`, ultralytics 8.4.174/torch 2.9.1+cpu/onnx 1.19.1): 렌더링한 9 프레임에서
  - 같은 640×640 letterbox 텐서를 원본 .pt에 넣은 결과 대비 위반 0/9 (score 최대차 1.2e-6),
  - ultralytics 자체 ONNX predict 대비 위반 0/9,
  - 기본 rect(640×480 stride padding) .pt predict와는 9/9 다름 — 입력 형상이 다른 진단값이며 동일성 주장에 쓰지 않는다.
  - 첫 두 export 시도(`export-20261022a`, `b`)는 비교 방법 오류(rect 입력, 경계 clip 누락)로 폐기, 동결 대상 아님. export 날짜 metadata 때문에 재export bytes는 매번 다르다.
- MiniLM: `models.Embedder`(공식 ONNX) vs sentence-transformers 5.1.2(safetensors, torch CPU), 17문장 min cosine 0.99999988, max abs 1.8e-7.

## 실행 결과 — 로컬/오프라인 단계 (기록 보존)

모든 명령은 `rtk proxy` 접두. `C=/mnt/data3tb/multi-edge-sensors-20261022`, `P=$C/envs/pod311/bin/python`. 정확한 명령은 `experiments/multi-edge-sensors/README.md`.

1. **준비** `prepared-20261022b` (`$C/prepared-20261022b`, 사본 `artifacts/multi-edge-sensors/prepared-20261022b/prepared.json` sha256 `760da229…7371`). bundle: wheels-x86_64 80.0 MB, wheels-aarch64 72.7 MB, mediamtx-x86_64 56.3 MB, mediamtx-aarch64 66.1 MB, models 101.6 MB, fixtures 0.4 MB. 같은 입력으로 다른 디렉터리에 재생성 시 bundle hash 동일(테스트).
2. **dry-run** (`artifacts/multi-edge-sensors/dryrun-20261022b.json`, kubectl 호출 0): source 5개 → 지정 엣지, 후보 9, 제외 dev0001/0004/0007, 저장 inventory 기준 preflight passed. 실행량: source Pod 5, qualification Pod 27(요청 V108/T72/F36), RTSP probe 27, 전송 probe GET 567, scheduler 관측 Pod 9, 고유 job ≤18, 평가 workflow ≤54(상한 72), 전체 workflow 요청 ≤72.
3. **읽기 전용 live preflight**(`kubectl get nodes`, 변경 없음): 현재 Ready9 topology preflight passed. 같은 namespace에 다른 작업의 `diag-ec-fleet-20261022-*` Pod(Error/Terminating)가 있으며 건드리지 않았다.
4. **로컬 smoke** `artifacts/multi-edge-sensors/local-smoke-20261022c/` (status passed): 일반 CPython 3.11.15 x86_64로 실제 `bootstrap.py`(cache HTTP→hash 검증→`pip --require-hashes --no-deps --no-index`)를 role 5개에 실행. MediaMTX v1.21.2 + PyAV publisher 프로세스(pid/cmdline 기록), H.264 640×480 5 fps, V 프로세스의 RTSP probe setup 1768 ms(FFmpeg probing 포함), 24 프레임 decode, skip 0, loop 일관성 true, probe decode hash == 동결 hash 6/6. workflow 4회(warmup1+평가3) 모두 ok, E2E 47.8–57.3 ms(평가), stage V 36–45 ms / T 8–14 ms / F <0.1 ms. 4/4 행이 같은 호스트 CPU reference 허용치 이내. 첫 시도 `local-smoke-20261022b`는 상대 `--output` 경로 버그로 video bootstrap이 시작 못 함(rc=2) — 보존, 수정 후 `c`로 재실행.
5. **테스트** (`$P -m unittest …`, `experiments/multi-edge-sensors`에서):
   - `test_prepare` 7 OK (덮어쓰기 거부, hash 불일치 시 출력 미생성, bundle hash, 원본 가중치 연결, wheel hash pin, 입력 범위, 재생성 동일).
   - `test_collect` 3 OK (dry-run 매핑·후보·kubectl 0회, inventory preflight, 서버 source 거부).
   - `test_execute` 11 OK, 497.9 s: fake kubectl로 실제 로컬 프로세스 전체 수집 + `reference.py` + `verify.py`. 주입한 ser0004 `ImagePullBackOff`는 V/T/F 모두 사유 기록·후보 제외(8개 유지, 대체값 없음), 모든 job×4 시도 1회씩 기록, 보호 Pod env 값 미저장, 정리 잔여 0. 감사기가 서버로 치환된 source / model hash / input hash / 누락 표본 / 같은 배치 중복 job을 각각 거부.
6. **오프라인 fake 수집 보존본** `artifacts/multi-edge-sensors/offline-fake-20261022b/` (+ `.collect.log`, `.verify.json`): verify passed, `offline: true`, 16 job, 56 attempt, 품질 56/56, RTSP probe 24/24 ok·decode==동결 6/6. **배치·지연 수치는 한 x86 호스트의 loopback이라 의미 없음** — 경로·감사 점검용.

## 연구 문서(`docs/research/multi-edge-rtsp-text-sources-20261022.md`)와의 차이 — pilot 선택으로 수용됨

coordinator가 FP32 ONNX CPU 경로, 공개 정지 영상 3장의 결정적 변환, 실시간 PyAV/libx264 publisher, RTSP/동결 프레임 비교 분리를 이번 pilot 선택으로 수용했다(`fix-review-r1.md`). 유효 범위는 좁다: 3장 입력으로 검출 품질·다양성을 주장하지 않으며, MediaMTX deprecated API 필드(`ready/tracks/bytesReceived`)를 v1.21.2에서 그대로 쓴다. 더 큰 데이터셋·동시 부하는 이후 작업.

| 항목 | 연구 권고 | 현재 구현 | 비고 |
|---|---|---|---|
| YOLO 실행 | ultralytics 8.3.220 torch CPU 우선, ONNX는 품질 확인 후 대안 | ultralytics 8.4.174 export ONNX + ORT | 같은 텐서 원본 대비 0/9 위반(score 차 ≤1.2e-6)으로 확인. torch/OpenCV wheel 회피 |
| 영상 입력 | 기존 COCO 60장 pool, split 보존 | 공개 정지 영상 3장 × seed 프레임 6 | 다양성 부족. 검출 품질 주장 불가 |
| 송출 | 사전 인코딩 MP4 `-c copy` | publisher가 실시간 libx264 인코딩(결정성은 loop hash로 확인) | 엣지 CPU 부하가 source에 포함됨 |
| MediaMTX API | `online/available/inboundBytes` | deprecated `ready/tracks/bytesReceived` 사용 | v1.21.2에서 동작은 함 |
| YOLO score 허용치 | ≤1e-4 제안 | ≤1e-3 | 관측 차이는 둘 다 충족 |

## 독립 검토 반영 (r1, r2) — 로컬/오프라인 검증만

상세·명령: `.scratch/multi-edge-sensors/fix-report.md`.

- **소유권**: create-only + API 반환 UID 기록, `delete --raw` + `DeleteOptions.preconditions.uid`(서버 강제), run 라벨 재사용 거부, 교체·foreign 객체 보존, 출력이 잘린 생성은 확인된 UID만 정리하고 나머지는 `unresolved_creation`(검증된 0으로 보고하지 않음, `cleanup_verified=false`).
- **수치 검증**: V/T/F 출력의 형태·유한성·정규화·클래스 이름·검출 수·순위 일관성을 fail closed로 검증, F는 top3 이름·검출 수·text class까지 reference와 비교. 형식 위반은 verify evidence error.
- **후보 품질 gate**: source 동결 직후 보정 seq(0–2)만의 CPU reference(`calibration-reference.json`)로 모든 보정 행의 provenance·수치를 확인(`candidate-quality.json`), valid 노드만 profile·probe·kube-default 공통 후보. 최종 평가 품질 감사(`reference.py`)는 별도.
- **비용 경계**: V 계획 비용 = 행별 PNG decode + 추론(median), fetch는 전송으로 분리. F→목적지 bytes는 실제 `/run` 응답 body(결과 + receipt envelope; w1 2816 B 중 task 340 B)로 보정, V/T→F는 실제 `/infer` body.
- 통합 실패 2건과 원인(controller exec 단일 인자 128 KiB 초과, `reference` 모듈 이름 충돌)을 보존·수정. 최종 오프라인 통합 `artifacts/multi-edge-sensors/offline-test_execute-r2b/`(13/14 + 기대 문구 정정 후 보존 증거 재검사 OK), 빠른 테스트 30 OK. 최종 bundle `prepared-20261022d`.

## 실제 클러스터 실측 (2026-10-22, run `mes-live-20261022t101935z`)

상세 표·명령·경로: `.scratch/multi-edge-sensors/live-report.md`, 원자료 `artifacts/multi-edge-sensors/mes-live-20261022t101935z/`
(+ `.collect.stdout/.stderr`, `.reference.stdout`, `.verify.json`, `.summary.json`, `-host-sources/` SHA256SUMS). bundle `prepared-20261022d`.

- **증거 레벨 구분**: 모델 변환 대조(로컬) / 로컬 smoke(x86 한 호스트) / fake kubectl(오프라인) / **이번 실제 클러스터 실행**.
- **source**: 영상 3개(dev0003/0006/0008, arm64 MediaMTX+PyAV publisher alive, H.264 640×480 5 fps), 텍스트 2개(dev0002/0009) 모두 각 엣지에서 실행·증명.
- **qualification**: V/T/F × 9 노드 27 Pod 모두 Ready(dev0008/0009 image pull 성공, arm64 ser0003·Jetson 포함), 보정 행 품질/provenance 실패 0 → 모든 workflow·task 9/9 후보.
  보정 compute median: YOLO11n Pi5 201–235 ms, Jetson 289 ms, ser0001 36, ser0002 40, ser0003 55, ser0004 70 ms; MiniLM Pi5 25–27, Jetson 54, 서버 6.8–14.6 ms.
- **RTSP 프로토콜 probe**: 27/27 ok, setup 1.56–1.84 s(FFmpeg probing 포함). 27 probe 중 18개(arm64 reader)는 동결 프레임과 decode 6/6 일치, 9개(x86 reader 3대 × source 3)는 0/6(54 frame 불일치, loop 일관, 원인 미확인) — 모든 decoder가 bit-identical이지는 않다. 공통 동결 PNG 비교 품질은 68/68.
- **평가**: 17 고유 job, 68 attempt(warmup 17 + 평가 51) 실패·누락 0, 품질 68/68. 서버 배치 정책들의 평균 E2E는 w1 90–101 ms, w2 53–60 ms, w3 54–58 ms로 서로 2–10% 이내(SD 2–7 ms);
  평균 기준으로 kube-default는 세 workflow 모두 task-fastest보다 2.4–9.5% 컸고, greedy/exact는 task-fastest 대비 −5.4…+2.4%; 중앙값 기준 exact는 task-fastest보다 3.3–5.6%, kube-default보다 6.4–10.0% 작았다(지표에 따라 w2 방향이 바뀜). source-local(엣지 YOLO)은 215–225 ms로 ≈4배.
  w1의 추가 ≈35–40 ms는 dev0003에서 동결 프레임 pull ≈41–44 ms(다른 영상 엣지 ≈5–6 ms)로 측정됨(원인 미진단). w1 source-local은 dev0003 CPU 부족으로 infeasible 기록.
- **비용 분리**: 결정 시간 0.03–13 ms, qualification ≈388 s, 전송 probe ≈109 s, job별 배포+측정 20–89 s — E2E에 미포함.
- **정리**: 생성 106개 UID 조건 삭제, remaining 0, unresolved 0, run-label 조회 0, 보호 Pod 252개 변화 0. 정상 삭제만 발생했고 실제 409(UID 불일치) 경로는 live 검증되지 않음.

## 남은 일 / 한계

1. 배치 비교는 동결 decoded 프레임 경로이며 실시간 RTSP E2E·frame age·동시 부하 비교가 아니다(별도 확장).
2. n=3·1회 배포·native LAN. 노드 수 증가나 DAG 공동 배치 이점의 인과적·일반적 결론 없음. exact의 T 위치는 비용모형 동률의 정렬 순서 선택.
3. x86 reader decode 불일치와 dev0003 전송 지연은 관측만 했고 원인 미진단.
4. 합성 입력(공개 정지 영상 3장·템플릿 문장), MediaMTX deprecated API 필드 사용, 평가 job마다 wheel/모델 재설치(배포 비용 큼).
5. `run_smoke.IMAGE`/`NS`를 import하며 그 파일의 미커밋 변경(레지스트리 `10.254.192.47:5000`)은 다른 작업 소유다.
6. 라이선스: Ultralytics 가중치·예제 이미지 AGPL-3.0, COCO 사진은 Flickr 조건 — 외부 배포 전 확인.
