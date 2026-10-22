# Developer report — multi-edge-sensors (task_b9cc9761af0c / ctx_0be17f309ef7)

- 구현 모델: Claude Opus 5.5 (`claude-opus-5-5`), Claude Code 구독 세션. 다른 모델/공급자 fallback 없음. 하위 agent 미사용.
- 범위: 구현 + 로컬 실제 프로토콜/모델 smoke + fake-kubectl 오프라인 검증. **클러스터 변경 0** (읽기 전용 `kubectl get ns/nodes/pods`만).
- 커밋/push 없음. spec·기존 코드·기존 artifact 미수정.

## 만든 것

- 코드: `experiments/multi-edge-sensors/` (README 참조) — prepare, collect(dry-run/execute), reference, verify, smoke_local, Pod runtime/bootstrap, fake kubectl, 테스트 3개.
- 문서: `docs/experiments/multi-edge-sensors-20261022.md`.
- artifact (모두 신규·고유 이름): `artifacts/multi-edge-sensors/{prepared-20261022b/, dryrun-20261022b.json, local-smoke-20261022b/ (실패 보존), local-smoke-20261022c/, offline-fake-20261022b/ (+.collect.log, .verify.json)}`.
- 대용량 캐시: `/mnt/data3tb/multi-edge-sensors-20261022/` (downloads, wheels, envs/{pod311,export311,st311}, export-20261022{a,b,c}, prepared-20261022{a,b}, minilm-check-20261022a.json). a/b export는 비교 방법 오류로 폐기된 시도, c가 고정본.

## 테스트 (실제 실행)

| 명령 (cwd `experiments/multi-edge-sensors`, `$P=/mnt/data3tb/multi-edge-sensors-20261022/envs/pod311/bin/python`) | 결과 |
|---|---|
| `rtk proxy $P -m unittest test_prepare test_collect` (최종 코드) | 10 OK, 4.7 s |
| `rtk proxy $P -m unittest test_execute -v` | 11 OK, 497.9 s |

Seam: 공개 CLI(prepare / collect / reference / verify)와 시스템 경계 kubectl(PATH의 fake). 내부 함수 mock 없음. 순서상 prepare는 red→green, dry-run 테스트는 구현과 같은 사이클에 작성(엄밀한 red 선행 아님), execute/audit 테스트는 collect 실행 경로 디버깅 전에 작성.

## 검증 수치 (로컬)

- YOLO export gate: 같은 텐서 .pt 대비 0/9, ultralytics ONNX 대비 0/9 위반. MiniLM vs sentence-transformers min cosine 0.99999988.
- 로컬 smoke c: passed — RTSP H.264, decode==freeze 6/6, workflow 4/4 ok, 품질 4/4.
- 오프라인 fake run: verify passed(offline), 56/56 품질, RTSP 24/24.

## 과정에서 바뀐 설계/발견

1. ultralytics 기본 .pt predict는 rect(640×480) 입력이라 ONNX 640×640과 다름 → 같은 텐서 비교로 gate 정의, rect는 진단으로 기록.
2. MediaMTX v1.21 기본으로 MoQ(:8892/8893)가 켜지고 cwd에 key를 씀 → `moq=False` 명시.
3. 호스트 0.0.0.0:8080 점유로 fake Pod bind 실패 → Pod 포트를 design 기록값(`MES_POD_PORT`, 기본 8080, 테스트만 18080)으로.
4. coordinator의 exec stdin race 경고 반영: 엣지 Pod로의 `kubectl exec` 제거, 서버의 controller Pod가 HTTP `/batch`·`/drive`로 구동. exec는 서버의 cache(1회 `exec -i` 업로드)·controller에만.
5. ImagePullBackOff 등 terminal waiting reason이 45 s 지속되면 해당 Pod 대기를 조기 종료하고 사유 기록.
6. kube-default 관측 배치는 동결 request 합 snapshot과 어긋나도 실제로 실행됐으므로 평가(`profile_feasible` 플래그로 기록).
7. smoke의 상대 출력 경로 버그(첫 시도 b 실패) 수정.

## 범위 밖 부작용 (정리됨/기록)

- ultralytics 첫 import가 `~/.config/Ultralytics/settings.json`을 만들었음 → 즉시 삭제, 이후 `YOLO_CONFIG_DIR`를 전용 디렉터리로 지정.
- 한 번 `/tmp/claude-1002/nodeps`(wheel 설치 확인)를 사용 후 삭제. `uvx pyflakes`가 uv 캐시에 pyflakes를 받음.
- 첫 fake run(구버전 코드)은 TaskStop으로 중단, 남은 로컬 프로세스 SIGTERM 정리 확인.

## 남은 blocker / 결정 필요

`docs/experiments/multi-edge-sensors-20261022.md`의 "연구 문서와의 차이"와 "남은 일" 절 참조. 요약: 클러스터 실행 미수행(검토·지시 대기), arm64 미실행, dev0008/0009 image pull 가능성, 4GB 엣지 메모리, 입력 다양성(3장 vs 연구 권고 COCO 60장), 실시간 libx264 vs 사전 인코딩 MP4, MediaMTX deprecated API 필드.

## 재개 명령

```bash
C=/mnt/data3tb/multi-edge-sensors-20261022; P=$C/envs/pod311/bin/python; E=experiments/multi-edge-sensors
rtk proxy $P $E/prepare.py --cache $C --output $C/prepared-<new>      # 현재 코드 hash
rtk proxy $P $E/collect.py --dry-run --prepared $C/prepared-<new>
# 검토 + coordinator 지시 후에만:
rtk proxy $P $E/collect.py --execute --prepared $C/prepared-<new>
rtk proxy $P $E/reference.py --run artifacts/multi-edge-sensors/mes-<utc> --prepared $C/prepared-<new>
rtk proxy $P $E/verify.py --run artifacts/multi-edge-sensors/mes-<utc> --prepared $C/prepared-<new>
```
