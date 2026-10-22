# dev0009 edgecore(KubeEdge v1.23.0) crash·재시작 볼륨 해제 수리 준비

> **상태(2026-10-22 r1):** 초판 패치(`patches-v1.23.0/`)와 초판 바이너리 3종(`build/`의 `edgecore-v1.23.0-2-g389862263-arm64{,-static}`, `edgecore-v1.23.1-2-g22818d959-arm64-static`)은 독립 리뷰에서 **두 패치 모두 배포 불가로 판정되어 REJECTED**다. 설치하지 않는다. 기록 보존을 위해 파일과 아래 초판 절은 그대로 두었다. 현재 후보는 r1(`patches-r1-v1.23.0/`, `build-r1/`)이며, **coordinator의 최종 독립 리뷰와 호스트 검증 전까지 "수리 완료"가 아니다.**
>
> **정정:** 초판 중간 보고에서 호스트 사실로 `edgecore --version`을 요청했는데, 이 명령은 데몬을 하나 더 띄운다(coordinator 확인: 두 번째 프로세스는 port 충돌로 종료, 원 프로세스 유지). 이후 절차에서는 `--version`을 쓰지 않는다. 바이너리 실행 확인은 `--minconfig`(`pkg/util/flag/flags.go:92-104`, 설정 출력 후 `os.Exit(0)`, 데몬 기동 전)로만 한다.

## r1 개정 (task_a228ddcf8c95)

### 초판이 기각된 이유 (리뷰 지적을 소스로 확인)

- **P1(#6836 backport):** RLock을 쥔 채 blocking send를 하므로 ReadChan이 가득 차고 consumer가 없으면 교착된다. exec dial이 consumer(`receiveFromCloudStream`) 시작 전에 실패하는 경우가 그렇다. 이때 `DeleteLocalConnection`의 Lock과 tunnel reader 전체가 멈춘다. r1 레드 테스트가 이를 **실제 `ServeConnection` 경로(TLS dial 지연 후 실패)에서 재현**했다: `ServeConnection did not finish within 5s`, stress는 `go test` 2분 timeout(`evidence-r1/02`). v1.23.0 원본은 같은 테스트에서 `send on closed channel`, 이중 close에서 `close of closed channel`로 실패한다(`evidence-r1/01`).
- **P2(HTTP `/pods` 폴링 + 2분 fallback):** timeout이나 취소가 나면 fail-open으로 ready를 표시해 live 볼륨을 다시 지울 수 있다. HTTP 요청에 context와 요청별 timeout도 없었다. 추가로 확인한 위험: kubelet은 sources가 ready가 아니면 `deletePod`를 거부하고(`vendor/.../kubelet.go:2173`), housekeeping/HandlePodCleanups도 건너뛴다(`kubelet.go:2423`). device/cpu/memory manager의 stale state 정리와 container GC 일부도 보류된다. 그래서 UID 기반 대기가 stale·filtered UID 때문에 **영구 정지하면 삭제된 Pod가 계속 남는다.** fail-open도 영구 정지도 안전하지 않다.

### r1 P1: 연결별 ReadChan 수명주기 guard (`pkg/stream/readchan.go`)

- exec/attach/logs/metrics 4종에 공통 `readChanGuard`를 넣었다. send는 연결이 열려 있는 동안에만 대기한다(backpressure 유지, 정상 경로에서 드롭 없음). close는 먼저 `done`을 닫아 대기 중인 sender를 풀고, in-flight send가 끝나기를 기다린 뒤 `ReadChan`을 **한 번만** 닫는다(`sync.Once`, 멱등). 닫힌 뒤의 send는 버린다. 연결이 이미 없어졌으므로 받을 쪽이 없다.
- tunnel 쪽 `WriteToLocalConnection`은 v1.23.0 그대로 둔다. session lock을 send 동안 쥐지 않으므로 #6836의 교착이 없다. `tunnel.go`는 구조체 값 복사 로그 두 곳(`%+v", *execCon` 등, 이제 mutex 포함)만 포인터로 바꿨다(`go vet` copylocks 대응).
- 테스트(`edge/pkg/edgestream/tunnel_lifecycle_test.go`), 4종 연결 모두 대상:
  - full buffer + consumer 없음 + delete
  - 실제 exec `ServeConnection`: TLS dial 지연 중 129개 이상 stdin으로 buffer full, 이후 dial 실패
  - live consumer로 500개를 순서대로 전부 전달(드롭 없음)
  - CloseReadChannel 이중 호출과 close 후 send
  - 400개 연결에 writer 2개와 delete 2회를 동시에 실행
  - 결과: v1.23.0 FAIL, #6836 FAIL(교착), r1은 `-race`에서 PASS(`evidence-r1/03`). 기존 `pkg/stream` 테스트는 `-race` 없이 3회 PASS(`evidence-r1/04`). `-race` 경고 15건은 패치 전 17건과 같은 테스트 mock 위치(줄 번호 +1)이고 `readchan.go`는 0건이다(`evidence-r1/05`).

### r1 P2: kubelet이 초기 Pod를 실제로 처리한 뒤에만 ready (HTTP·UID·timeout 없음)

- 근본 원인을 더 정확히 확인했다. upstream kubelet v1.32.10의 `PodConfig.SeenAllSources`는 `seenSources.HasAll(sources) && pods.seenSources(sources)`다. `seenSources`는 `syncLoopIteration`이 update를 **처리한 뒤** `AddSource(u.Source)`로 기록한다(`kubelet.go:2356`). 그런데 kubeedge fork(`kubeedge/kubernetes` v1.32.10-kubeedge1, 커밋 `a2eaf54ed8` "replace pod ready with edgedCh")는 이를 `return c.initPodReady`로 바꿨다. 주석은 upstream 설명 그대로 남아 있다. 근거: `.scratch/edgecore-crash-fix/upstream-ref/`(두 파일, vendor와 fork 태그가 일치함을 확인).
- 수정 1(fork vendor `pkg/kubelet/config/config.go`): `initPodReady && upstream 조건`으로 복원하고 `podStorage.seenSources`를 되살렸다. 첫 SET은 pod가 없어도 반드시 ADD를 내보내므로(`firstSet`, `config.go:196-201`) kubelet이 그 ADD를 처리하면 ready가 된다. file source(static pod)도 첫 listing에서 SET을 보낸다(`file.go:78,129`). 따라서 **ready ⇔ kubelet syncLoop가 초기 pod 목록을 podManager에 넣은 뒤**다. timeout이 없으므로 fail-open이 없다. kubelet이 늦게 회복해도 그때 ready가 된다. UID 매칭을 하지 않으므로 stale, 삭제됨, filtered UID 때문에 멈추지 않는다.
- 수정 2(edged `syncPod`): 초기 목록보다 먼저 온 pod 메시지는 보관했다가 목록을 보낸 직후 순서대로 처리한다. 이것이 없으면 cloud의 이른 pod update가 먼저 처리되어 `seenSources[api]`가 미리 켜지고, 초기 ADD가 소비되기 전에 ready가 될 수 있다. fork 수정만 적용한 상태에서 이 경우가 FAIL로 재현됐다(`evidence-r1/07`).
- 테스트(`edge/pkg/edged/sources_ready_test.go`): 실제 `syncPod`, beehive, fork `PodConfig`, `NewSourcesReady`를 쓴다. kubelet syncLoop만 `syncLoopIteration`과 같은 순서(update 적용 → AddSource)로 흉내 낸다. 시나리오마다 별도 프로세스(syncPod는 전역 큐를 쓰고 종료하지 않음).
  - (a) syncLoop 정지 1.5초 동안 ready 금지, 이후 늦은 회복 시 ready이고 초기 pod 포함
  - (b) 목록보다 먼저 온 update가 창을 열지 않고 유실되지도 않음
  - (c) stale pod 직후 delete와 중복 이름(filtered) pod가 있어도 정지하지 않음
  - (d) 빈 목록에서 정지하지 않음
  - 결과: v1.23.0은 (a)(b) FAIL(`evidence-r1/06`), fork 수정만이면 (b) FAIL, r1은 `-race`에서 전부 PASS(`evidence-r1/08`)
- 요구사항 대응:
  - "HTTP context/요청별 timeout", "stalled endpoint": HTTP 의존을 없애서 해당 없음. stalled kubelet은 (a)로 시험했다.
  - "missing UID": (c)로 시험했다.
  - "eventual admission": (a)의 늦은 회복으로 시험했다.
  - "cancellation": r1은 goroutine, timer, HTTP를 추가하지 않는다. syncPod 종료는 기존 `beehiveContext.Done()` 그대로다. 전역 context라 별도 테스트는 하지 않았다.

### r1 산출물

| 항목 | 값 |
|---|---|
| branch | `.scratch/edgecore-crash-fix/src/kubeedge-r1` `local/v1.23.0-edgecore-crash-fix-r1` = v1.23.0 + `ffd81d3a7`(P1) + `da0ee076a`(P2), 로컬 전용 |
| patches | `patches-r1-v1.23.0/0001…`(sha256 `311420806af650ef2b970e237c152ad83108cce41dc1281dac4ad25d70f029db`), `0002…`(`e25a3493cbc8fef38d64ddbe450944432ecc3f087731e4814603614ee21eba18`) |
| binary | `build-r1/edgecore-v1.23.0-2-gda0ee076a-arm64`, sha256 **`afda505d1134340bcb1d9cf0acd1e6d2cab8dc1d46a1dcbdbe1116b3daf8cc6e`**, dynamic, 최대 GLIBC_2.34(호스트 2.39), gitVersion `v1.23.0-2+da0ee076a31934`. 테스트가 모두 통과한 뒤 빌드했다. qemu에서 `--minconfig` exit 0 |
| host script | `host-r1/edgecore-fix.sh`(`ORIG_SHA`를 노드별로 override, `/pods` 의존 제거, verify에서 재시작 이후 UnmountVolume을 Pod UID별로 집계). 이 집계 grep을 05:58 syslog에 적용하면 사고 당시 7개 Pod가 그대로 나온다. 초판 `host/`는 보존 |

### r1 배포·검증 (최종 독립 리뷰 후, coordinator SSH)

1. 게이트: 실험 Pod 없음(coordinator 확인됨). `ORIG_SHA=<노드 실측> ./edgecore-fix.sh preflight`를 실행한다. `--version`은 금지.
2. `/tmp/edgecore.new`로 복사하고 `afda505d…cc6e`를 대조한다. canary Pod를 만들고(`make_pod.py diag-ec-canary etri-dev0009-jetorn`) `/work/marker`를 쓰고 UID를 기록한다.
3. `CONFIRM=install ./edgecore-fix.sh install /tmp/edgecore.new afda505d1134340bcb1d9cf0acd1e6d2cab8dc1d46a1dcbdbe1116b3daf8cc6e`: 백업, 원자적 교체, restart. 이 restart가 P2의 첫 실검증이다.
4. `verify <restart-at> <canary-uid>` 수용 기준:
   - panic 0
   - 재시작 이후 **실행 중이던 Pod의 UnmountVolume 0건**(canary 포함)
   - canary `//deleted` 0
   - canary 안에서 `/work/marker` 존재, `mkdir /work/after-restart` 성공
   - "Finished populating"이 마지막 "Skipping pod synchronization" 이후에 나온다
5. 수동 restart를 한 번 더 하고 4를 반복한다(n=2). 이어서 `host-r1/exec_stress.sh topology-profile-lab diag-ec-canary worker 50`을 실행하고 panic 0, NRestarts 불변을 확인한다.
6. 실패하면 `CONFIRM=rollback`을 실행한다. 롤백 restart는 원본 기동 경로라 실행 중 Pod 볼륨이 다시 지워지므로, 롤백 후 Pod를 재생성한다.

### r1 남은 한계 (가설·미검증 포함)

- **실제 kubelet 기동에서 검증하지 않았다.** P2 테스트는 syncLoop을 소스 순서대로 흉내 냈을 뿐이다. 실제 PLEG/runtime 지연과의 상호작용은 호스트 4~5단계에서만 확인된다.
- P2는 vendored fork 코드를 고친다. `go mod vendor`를 다시 하면 덮어써지므로 upstream 반영 대상은 `kubeedge/kubernetes`다.
- P2 이후 Pod 삭제/housekeeping은 kubelet syncLoop가 healthy해진 뒤에야 진행된다. upstream kubelet과 같은 의미이지만, v1.23.0보다 기동 직후 삭제 처리가 늦어질 수 있다.
- metamanager 초기 목록이 끝내 오지 않으면 ready가 되지 않는다. v1.23.0과 같다. 그 사이 보관 메시지는 메모리에 제한 없이 쌓인다.
- P1: 연결의 consumer가 느리거나 아직 없으면(dial 중) 단일 tunnel reader가 그 동안 멈춘다. 이는 기존 backpressure 동작이고 dial 시간에 묶인다. RemoveConnect가 3번 넘게 오면 `receiveFromCloudStream`이 `Stop` 송신에서 멈춰 goroutine이 누수될 수 있다. 이 경로는 패치 전부터 있었고 고치지 않았다.
- 이미 `//deleted` 상태인 기존 Pod 볼륨은 복구하지 않는다(재생성 필요).
- 바이너리는 dev0009 arm64용 하나뿐이다. fleet의 다른 노드는 노드별 원본 sha, arch, glibc를 preflight로 확인해야 하고 amd64 노드는 별도 빌드가 필요하다. stale registry IP(192.168.0.56) 문제는 이 패치와 별개다(coordinator 조치 중).
- 패치 버전 문자열이 cloudcore 호환성 검사에 주는 영향은 확인하지 못했다.

---

## 초판 기록 (task_504a17aa18ae, REJECTED — 수정하지 않고 보존)

- 일시: 2026-10-22. 범위: **오프라인 준비만.** 호스트 변경·재시작은 하지 않았다. 아래 "수리 완료" 판정은 호스트 검증 전까지 내리지 않는다.
- 선행 진단: `docs/experiments/dev0009-workdir-diagnosis.md`, syslog 원본은 `.scratch/dev0009-workdir-diagnosis/evidence/10-host-syslog.txt`.
- 작업 디렉터리: `.scratch/edgecore-crash-fix/`(소스 clone, 패치, 테스트 로그, 빌드 산출물, 호스트 스크립트). 1차 출처 표는 `.scratch/edgecore-crash-fix/research-findings.md`에 있다.

### 결론 요약 (초판, REJECTED)

두 결함은 **서로 독립이며 각각 패치가 필요하다.** crash만 고쳐도 다른 이유로 재시작하면(수동 재시작, OOM, 업그레이드) 볼륨 삭제는 그대로 재발한다. 볼륨 문제만 고치면 exec 사용 중 crash가 계속 난다.

| # | 결함 | 근본 원인(소스 확인) | 공식 수정 | 준비한 수정 |
|---|---|---|---|---|
| 1 | `panic: send on closed channel` | `TunnelSession.WriteToLocalConnection`이 lookup 후 lock을 풀고 `ReadChan`에 send한다. 그 사이 `DeleteLocalConnection`이 close한다. 버퍼가 가득 차 send가 대기 중일 때도 같다 | v1.23.1·master 모두 **없음**. 미병합 PR [#6836](https://github.com/kubeedge/kubeedge/pull/6836)만 있다 | #6836 그대로 backport(1파일 +3/-1) |
| 2 | 재시작 직후 실행 중 Pod 볼륨 TearDown | edged가 pod list를 큐에 넣은 **직후** `SetInitPodReady(true)`를 호출한다. kubelet syncLoop는 PLEG/runtime이 healthy해질 때까지 pod update를 읽지 않는다. 그래서 volume populator가 빈 podManager로 "initial desired state" 완료를 선언하고, reconciler가 재구성된 볼륨을 전부 unmount한다 | **없음**(이슈도 없음) | 로컬 패치: kubelet read-only `/pods`(=podManager)에 초기 Pod UID가 모두 보일 때 ready로 표시. 2분이 지나면 기존 동작으로 fallback |

### 증거 수준 구분

**확인됨(소스+로그+로컬 테스트):**
- syslog의 스택·로그 줄 7개가 v1.23.0 소스 줄과 정확히 일치한다(`research-findings.md` A).
- 볼륨 해제 순서가 소스 경로와 시각 단위로 일치한다. 34.727 Start sync pod → 34.743 Skipping pod synchronization(PLEG) → **34.799 Finished populating** → 34.866 UnmountVolume(source-e9 work 포함) → 35.169 같은 Pod VerifyControllerAttachedVolume.
- 결함 1 재현 테스트(`edge/pkg/edgestream/tunnel_race_test.go`): v1.23.0에서는 결정적 테스트와 stress 테스트 **모두 `send on closed channel`로 FAIL**(`evidence/01`). #6836 적용 후에는 `-race`에서도 PASS(`evidence/02`).
- 결함 2 재현 테스트(`edge/pkg/edged/init_pod_ready_test.go`): 실제 `syncPod`·beehive·`PodConfig`에 fake kubelet `/pods`를 붙였다. v1.23.0에서는 "podManager가 비었는데 sources ready" 상태로 **FAIL**(`evidence/04`). 패치 후에는 대기했다가 admit된 뒤 ready가 되어 PASS. timeout fallback 테스트도 PASS(`evidence/05`, `06`, `-race`).
- 두 패치는 v1.23.1에도 충돌 없이 적용되고 테스트가 PASS한다(`evidence/08`).
- `pkg/stream`의 `-race` 실패는 **패치 전 v1.23.0에서도 동일하다**(테스트 코드 자체의 race, `evidence/03`). 이 패키지는 수정하지 않았다. `-race` 없이 실행하면 PASS.

**가설/미검증:**
- 05:58:24의 정확한 interleaving(lookup 틈인지, 버퍼 full 대기 중 close인지)은 정하지 않았다. 두 경우 모두 테스트로 덮었다.
- 결함 2 패치는 **실제 kubelet 기동 타이밍에서 검증하지 않았다.** 단위 테스트의 fake `/pods`만 썼다. 호스트 검증이 필수다.
- 재시작 후 runtime/PLEG가 2분 넘게 unhealthy하면 fallback 때문에 기존 위험이 되살아난다. 2분은 선택한 값이고 측정값이 아니다.
- #6836은 RLock을 쥔 채로 send를 기다린다. consumer(`receiveFromCloudStream`)가 `Stop` 채널 송신에서 멈추는 이론적 경로가 있다. 이때 결과는 crash가 아니라 tunnel 정지다. 관측되지 않았다.
- 호스트 바이너리 sha256 `d17f777d…`은 공식 릴리스 tarball의 edgecore(`4aa4793d…`)와 다르다. 설치 경로(keadm installation-package 이미지 추정)는 확인하지 못했다(docker 권한 없음).
- 패치 버전 문자열은 `v1.23.0-2+389862263e7e05`다. cloudcore 쪽 버전 호환성 검사에 영향이 있는지는 확인하지 못했다.

### 업그레이드/패치 선택지

| 옵션 | 결함1 | 결함2 | 비고 |
|---|---|---|---|
| A. v1.23.1 공식 업그레이드만 | ✗ | ✗ | 보안 수정만 포함. 두 결함과 무관 |
| B. master/다음 릴리스 대기 | ✗(현재 master에 없음) | ✗ | 해결 시점 불명 |
| ~~C. v1.23.0 + 초판 패치 2개~~ **REJECTED** | ✓ | ✓(호스트 검증 필요) | 클러스터 버전 변화 최소. `build/edgecore-v1.23.0-2-g389862263-arm64-static` |
| D. v1.23.1 + 패치 2개 | ✓ | ✓(호스트 검증 필요) | 보안 수정까지 포함. 나머지 노드와 버전이 달라지므로 일괄 결정이 필요 |
| E. 바이너리 유지 + 운용 우회 | ✗ | 우회만 | edgecore 재시작을 감지하면 해당 노드 Pod를 재생성. 실험 harness 변경이 필요하고, 근본 해결은 아님 |

빌드 산출물(`.scratch/edgecore-crash-fix/build/SHA256SUMS`):

| 파일 | sha256 | glibc 요구 |
|---|---|---|
| `edgecore-v1.23.0-2-g389862263-arm64-static` | `efe46a0f…8808` | 없음(static) — **REJECTED, 설치 금지** |
| `edgecore-v1.23.0-2-g389862263-arm64` | `cddadcd1…d18d` | GLIBC_2.34 이상(빌드 툴체인 glibc 2.39). 공식 바이너리는 2.17 |
| `edgecore-v1.23.1-2-g22818d959-arm64-static` | `37f6b2e8…c008` | 없음 |

빌드에는 Go 1.23.12(공식 바이너리와 동일)와 `aarch64-linux-gnu-gcc` 13.3을 썼다. ldflags는 `hack/lib/golang.sh`의 `kubeedge::version::ldflags`다. static 링크 경고(sqlite `dlopen`)는 확장 로딩 경로에만 해당하고 edgecore는 이 경로를 쓰지 않는다. qemu에서 `--minconfig` 실행을 확인했다(`evidence/07`). 공식 컨테이너 빌드와 바이트 단위로 같지는 않다.

### 배포·검증 계획 (초판, REJECTED — r1 절의 계획을 사용)

스크립트: `.scratch/edgecore-crash-fix/host/edgecore-fix.sh`(호스트에서 실행), `host/exec_stress.sh`(kubectl 워크스테이션에서 실행).

0. **게이트:** dev0009에 실행 중인 실험 run이 없어야 한다. 재시작은 기존 Pod에 영향을 준다. 운영 Pod(flannel, device plugin, edgemesh 등)는 이전 재시작 때문에 이미 `//deleted` mount 상태일 수 있다.
1. `./edgecore-fix.sh preflight`(읽기 전용): glibc, 현재 sha(`d17f777d…`), unit 설정, `readOnlyPort`, `curl localhost:10350/pods` 200 여부, 기존 `//deleted` 개수를 확인한다. 패치 2는 read-only port에 의존한다. syslog의 "Starting to listen read-only port=10350"으로 현재 활성 상태는 이미 확인됐다.
2. static 바이너리를 `/tmp/edgecore.new`로 복사하고 sha256 `efe46a0fc9cb90124b3fa8d19e265ea8bdf9fa9a6dd4c7dd9602b24b07cd8808`을 대조한다.
3. canary Pod를 만든다: `python3 .scratch/dev0009-workdir-diagnosis/make_pod.py diag-ec-canary etri-dev0009-jetorn > canary.json && rtk proxy kubectl apply -f canary.json`. Ready가 되면 `/work/marker`를 만들고 Pod UID를 기록한다.
4. `CONFIRM=install sudo ./edgecore-fix.sh install /tmp/edgecore.new efe46a0f…`를 실행한다. 원본 sha를 확인하고, 백업 `/usr/local/bin/edgecore.v1.23.0-d17f777d0ac9.bak`을 만들고, 원자적으로 교체한 뒤 restart한다. **이 restart가 바로 패치 2의 첫 실검증이다.**
5. `./edgecore-fix.sh verify <restart-at> <canary-uid>`. 수용 기준:
   - `Version: v1.23.0-2+389862263e7e05`
   - "kubelet reports all N initial pods, marking pod sources ready"가 "Finished populating initial desired state of world"보다 **먼저** 나온다
   - canary UID의 UnmountVolume 0건, 호스트 mountinfo의 canary `//deleted` 0건
   - canary 안에서 `/work/marker`가 있고 `mkdir /work/after-restart`가 성공
6. 수동 restart를 한 번 더 하고 5를 반복한다(n=2, 우연 배제).
7. `host/exec_stress.sh topology-profile-lab diag-ec-canary worker 50`을 실행하고, 이후 verify에서 `panic:` 0건이고 NRestarts가 변하지 않았는지 확인한다. 패치 전 바이너리로 대조 실험은 하지 않는다. 이미 운영 crash로 근거가 있고, 대조 실험 자체가 볼륨을 삭제한다.
8. canary를 삭제한다. 기존 `//deleted` 상태 Pod를 재생성할지는 coordinator/사용자가 결정한다. 이 패치는 이미 지워진 디렉터리를 복구하지 않는다.

**롤백:** `CONFIRM=rollback sudo ./edgecore-fix.sh rollback`. 백업 sha를 대조하고 복원한 뒤 restart한다. **롤백 restart는 패치 전 기동 경로를 다시 타므로 실행 중 Pod의 emptyDir/ConfigMap이 다시 삭제된다.** 롤백 후 해당 노드의 실험 Pod를 재생성해야 한다. 5단계 기준 중 하나라도 실패하면 롤백하고 결과를 보고한다.

다른 edge 노드(dev0008, dev0003 등)에도 같은 결함이 있다(선행 진단의 `//deleted` 공통 패턴). dev0009 검증이 통과한 뒤 같은 절차를 노드별로 적용할지 결정한다. dev0008은 Raspberry Pi 5(arm64)이므로 같은 static 바이너리를 쓸 수 있을 것으로 보이지만 확인하지 않았다.

### 재실행 명령

```sh
cd .scratch/edgecore-crash-fix/src/kubeedge          # local/v1.23.0-edgecore-crash-fix (v1.23.0 + 2 commits)
export GOWORK=off GOFLAGS=-mod=vendor
go test -count=1 -race -v ./edge/pkg/edgestream/ ./edge/pkg/edged/        # 패치 후: PASS
# 패치 전 재현: 테스트 파일은 유지하고 구현 파일만 v1.23.0으로 되돌린 뒤 실행 → FAIL, 그리고 복원
git checkout v1.23.0 -- edge/pkg/edgestream/tunnel.go edge/pkg/edged/edged.go
go test -count=1 -v ./edge/pkg/edgestream/ ./edge/pkg/edged/
git checkout HEAD -- edge/pkg/edgestream/tunnel.go edge/pkg/edged/edged.go
```
패치 전 결과 원본은 `evidence/01, 04`에 있다. 빌드 명령은 `.scratch/edgecore-crash-fix/README.md`에 있다.
