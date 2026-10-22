# 1차 출처 조사 기록 (2026-10-22, 공식 KubeEdge 소스·이슈·릴리스만)

조사 방법: `github.com/kubeedge/kubeedge`를 `src/kubeedge`에 clone(origin/master `e3001611`, 2026-10-20). 태그 v1.23.0(`8e9e9fb1`), v1.23.1(`1a7ee88d`)의 소스를 직접 읽었다. `gh search issues/prs --repo kubeedge/kubeedge`(읽기 전용)로 이슈와 PR을 검색했고, `gh release`로 공식 산출물을 확인했다.

## A. exec stream panic (`send on closed channel`)

| 출처 | 내용 | 판정 |
|---|---|---|
| v1.23.0 `edge/pkg/edgestream/tunnel.go:143-147` | `WriteToLocalConnection`이 `GetLocalConnection`(RLock 획득 후 해제) 다음에 lock 없이 `CacheTunnelMessage`를 호출한다 | 경합 지점 확인 |
| v1.23.0 `tunnel.go:214-224` | `DeleteLocalConnection`이 Lock을 잡고 `CloseReadChannel()`(close(ReadChan))을 호출한다. `ServeConnection` 종료 직후 실행된다 | close 측 확인 |
| v1.23.0 `pkg/stream/edgedexecconnection.go:41-43` | `e.ReadChan <- msg`. 버퍼 128이 가득 차면 송신자가 대기하고, 그 사이 close되면 panic한다 | 확인 |
| dev0009 syslog 스택 | `edgedexecconnection.go:42`, `tunnel.go:145`, `tunnel.go:197`, `edgestream.go:151/112`, `edgedexecconnection.go:76`(broken pipe), `tunnel.go:172` | **7개 줄 번호가 v1.23.0 소스와 모두 일치** |
| `git log v1.23.0..v1.23.1 -- pkg/stream edge/pkg/edgestream edge/pkg/edged` | 변경 없음. v1.23.1은 보안 수정(#7028: NodeUpgradeJob/ConfigUpdateJob shell, tar path traversal, viaduct payload) | **v1.23.1 업그레이드로는 해결되지 않음** |
| master `f61850c2e`(#7061, 2026-07-27 merge) | Serve의 message type dispatch 정리, unknown type panic 제거. lookup→send 경합은 그대로 | **master에도 수정 없음** |
| PR https://github.com/kubeedge/kubeedge/pull/6836 (OPEN, 2026-06-02, 미승인·미병합) | `WriteToLocalConnection`에서 lookup과 send 동안 RLock을 유지해 close와 상호배제 | **유일한 공식 후보 패치.** 1개 파일 +3/-1 |
| issue #7328 (closed) | viaduct의 다른 경로에서 생긴 `send on closed channel` | 다른 코드. 해당 없음 |

## B. 재시작 직후 live Pod 볼륨 TearDown

| 출처 | 내용 | 판정 |
|---|---|---|
| v1.23.0 `edge/pkg/edged/edged.go:319-332` | metamanager pod list를 `rawUpdateChan`에 넣은 **직후** `podCfg.SetInitPodReady(true)`를 호출한다 | 확인 |
| vendor `k8s.io/kubernetes`(kubeedge fork v1.32.10-kubeedge1) `pkg/kubelet/config/config.go:108-119` | `SeenAllSources()`가 `initPodReady`만 반환한다. 이 값이 kubelet `sourcesReady.AllReady()`가 된다(`kubelet.go:506`) | 확인 |
| vendor `pkg/kubelet/kubelet.go:2267-2270` | `syncLoop`은 `runtimeState.runtimeErrors()`가 있으면 "Skipping pod synchronization" 후 sleep하고, pod update(configCh)를 소비하지 않는다 | 확인 |
| vendor `volumemanager/populator/desired_state_of_world_populator.go:147-162` | `AllReady()`가 true인 상태로 한 루프가 돌면 `hasAddedPods=true`가 되고 "Finished populating initial desired state of world"가 기록된다. podManager는 `HandlePodAdditions`(syncLoop) 이후에만 채워진다(`kubelet.go:2469`) | 확인 |
| vendor `volumemanager/reconciler/reconciler.go:30-54` | `readyToUnmount()` 이후 DSW에 없는 ASW(재구성된) 볼륨을 unmount한다. emptyDir TearDown은 디렉터리를 삭제한다 | 확인 |
| dev0009 syslog | 34.727 Start sync pod → 34.743/34.843 "Skipping pod synchronization ... PLEG is not healthy" → **34.799 Finished populating** → 34.866 UnmountVolume(source-e9 work/config 포함) → 35.169 같은 Pod VerifyControllerAttachedVolume(뒤늦게 podManager에 들어옴) | **소스 경로와 시각 순서 일치** |
| upstream issue 검색(unmount, emptydir, TearDown, volume/configmap restart, InitPodReady) | 해당 버그 보고나 수정 없음. 관련성 낮은 #6540(재시작 후 orphan container)만 있음 | **공식 수정 없음 → 로컬 패치 필요** |

## C. 산출물 출처

- 공식 릴리스 `kubeedge-v1.23.0-linux-arm64.tar.gz`: sha512가 공식 checksum 파일과 일치. 안의 `edge/edgecore` sha256은 `4aa4793d…bf84`, 요구 GLIBC는 2.17.
- 호스트 `/usr/local/bin/edgecore` sha256은 `d17f777d…82ac`(coordinator 확인)로, **릴리스 tarball 바이너리와 다르다.** keadm `installation-package` 이미지 등 다른 경로로 설치된 것으로 추정한다(미확인). 이 머신에는 docker 권한이 없어 이미지 내용을 대조하지 못했다. 다만 syslog의 `Version: v1.23.0`과 스택 줄 번호 7개 일치로 v1.23.0 소스와 동작이 같다고 판단한다.

## D. r1 추가 확인 (2026-10-22)

| 출처 | 내용 |
|---|---|
| upstream `kubernetes/kubernetes` v1.32.10 `pkg/kubelet/config/config.go:98-106` | `SeenAllSources = seenSources.HasAll(sources) && pods.seenSources(sources)` (`upstream-ref/k8s-v1.32.10-kubelet-config.go`, sha256 `457834ef…3b5a`) |
| `kubeedge/kubernetes` v1.32.10-kubeedge1, 같은 파일(vendor와 동일) | `return c.initPodReady`만 반환. 도입 커밋 `a2eaf54ed8` "replace pod ready with edgedCh"(2024-01-31) (`upstream-ref/kubeedge-fork-…`, sha256 `04af33e9…cfd1`) |
| vendor `kubelet.go:2356` | `syncLoopIteration`이 update 처리 후 `kl.sourcesReady.AddSource(u.Source)` |
| vendor `config.go:196-201` | `firstSet`이면 pod가 없어도 빈 ADD를 보내 "source is ready"를 알린다 |
| vendor `kubelet.go:2173`, `:2423` | sources not ready이면 `deletePod` 거부, housekeeping 생략 → 영구 not-ready는 삭제 Pod 누수 |
| vendor `cm/{device,cpu,memory}manager`, `container/container_gc.go` | stale state 정리와 GC 일부가 AllReady에 묶임 |
| `apimachinery` spdy `RoundTripper.Dial` | http에서는 TCP 연결 후 바로 반환한다. TLS(https)에서는 handshake가 끝날 때까지 대기한다. r1 P1 실경로 테스트는 TLS를 써서 "dial 진행 중, consumer 없음" 상태를 만들었다 |
| `pkg/util/flag/flags.go:92-104` | `--minconfig`는 설정 출력 후 `os.Exit(0)`(데몬 기동 전). `--version`은 coordinator 관측상 데몬을 기동하므로 금지 |
