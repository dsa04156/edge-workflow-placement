# dev0009 `/work/control-receipts` ENOENT 진단 (wan-20261022t055541)

## 최종 확인: host syslog로 crash → 자동 재시작 → 원 Pod 볼륨 해제 확인

Coordinator가 추가한 읽기 전용 `/var/log` 진단 Pod로 원 실패 시각 syslog를 확인했다. root+capabilities drop ALL은 읽기 거부되어(09), 파일 소유 UID 103으로 실행해 읽었다(10). 호스트 권한·설정은 변경하지 않았고 추가 Pod도 삭제, diag-owner 잔여 0 확인했다. 아래 초기 진단의 “재시작 원인/삭제 주체 미확인”은 이 후속 증거로 갱신한다.

근거: `.scratch/dev0009-workdir-diagnosis/evidence/10-host-syslog.txt` (05:57:30–05:59:30 UTC 범위, 관련 로그만 필터).

- 05:58:24.296 UTC (14:58:24 KST): edgecore PID 1241960에서 `panic: send on closed channel`. stack은 `EdgedExecConnection.CacheTunnelMessage`, `edgedexecconnection.go:42` → `TunnelSession.WriteToLocalConnection`, `tunnel.go:145` → `Serve`, `tunnel.go:197`. 바로 앞에는 exec 연결 broken pipe와 tunnel close가 있다.
- 05:58:24.338: systemd가 edgecore exit status 2를 기록했다.
- 05:58:34.467–.517: systemd 자동 재시작(restart counter 13), 새 edgecore 시작.
- 05:58:34.866: 새 edgecore PID 1336203이 원 source-e9 UID `d4f764e3-223f-472b-9a0a-c16feefbbbda`의 `work` emptyDir에 `UnmountVolume` 시작.
- 05:58:34.973: 같은 UID와 work 볼륨에 `UnmountVolume.TearDown succeeded`. config 볼륨도 해제됐다. sink-e9 UID `304bb298-ab63-4646-9be3-fc94f4593104`의 work도 같은 시각 해제됐다.
- 05:58:35.878: 같은 원 source Pod의 config/work를 다시 `VerifyControllerAttachedVolume` 대상으로 인식하는 로그.
- 06:27:29: 원 source-e9의 제어 기록 디렉터리 mkdir ENOENT. 최초 Pod mountinfo는 없지만, 이제 그 Pod의 work 볼륨 해제 자체는 로그로 직접 확인됐다.

**판정:** KubeEdge v1.23.0 edgecore의 exec stream 경로에서 panic이 발생했고 자동 재시작 직후 살아 있던 실험 Pod의 볼륨이 TearDown됐다. 이것이 이번 제어 경로 실패를 설명하는 직접 증거다. 정확한 채널 동시성 경합과 재시작 시 볼륨 재구성 순서 결함은 소스/재현 추가 확인이 필요하다. 특정 kubectl 요청이 panic을 유발했는지까지는 확정하지 않는다. 전체 실험 재시작이나 호스트 수리는 하지 않았다.

## 초기 진단 기록 (위 후속 로그 확인 전)

- 일시: 2026-10-22 06:27–06:41 UTC
- 범위: 진단만. 구현·호스트 수정·벤치마크는 하지 않았다. 재현 도구와 원본 출력은 `.scratch/dev0009-workdir-diagnosis/`에 있다.
- 증거 수준: 아래의 "확인됨"은 실제 클러스터 관측이다. 원 Pod(`wan-20261022t055541-source-e9`, UID `d4f764e3-…`)는 run cleanup으로 이미 삭제되어서, 그 Pod의 `/work` mountinfo는 직접 보지 못했다.

## 증상

`707-guard-True-source-e9`: netctl 컨테이너에 exec로 들어가 `Path('/work/control-receipts').mkdir(parents=True, exist_ok=True)`를 실행하자 `FileNotFoundError: '/work/control-receipts'`가 두 번 났다(pathlib가 parent를 확인한 뒤 다시 시도해서 이중 traceback이 됨). 같은 단계에서 다른 노드 5개(e0, e1, c, e6, e8)는 성공했다. netctl 시작 로그(722)를 보면 05:56 무렵에는 `/work/net-config`, `/work/netctl-ready` 생성이 성공했다.

## 타임라인 (UTC)

| 시각 | 사건 | 근거 |
|---|---|---|
| 04:38:05 | dev0009 containerd 재시작(registry 수정 작업) | `evidence/03`, `.scratch/dev0009-registry-fix` |
| 05:56:00–03 | source-e9 생성, 두 컨테이너 시작 | `015-helper-source-e9.stdout` |
| **05:58:33** | **dev0009 edgecore 프로세스 시작(재시작)** | `evidence/03` `/proc/<pid>/stat` starttime + btime |
| 05:58:34 | 실행 중이던 nvidia device plugin이 `kubelet.sock created, restarting` 로그 직후 `open /config/config.yaml: no such file or directory`로 종료. 즉 실행 중 Pod의 ConfigMap 볼륨 내용이 사라졌다 | `evidence/07` |
| 06:27:29 | guard exec의 mkdir가 ENOENT로 실패 | `707-…stderr` |

edgecore가 재시작한 시점은 source-e9가 살아 있던 구간(05:56–06:27) 안에 있다. **누가, 왜 재시작했는지는 확인하지 못했다.** 실패 당시 journal과 syslog는 node-exporter(uid 65534)로 읽을 수 없었다(`evidence/08`). dev0009에는 kubelet 프로세스가 따로 없고 edgecore(내장 edged)와 containerd만 돈다(`evidence/08`).

## 가설과 판정

| # | 가설 | 예측 | 결과 |
|---|---|---|---|
| H1 | edgecore 재시작 때 실행 중 Pod의 볼륨 호스트 디렉터리가 지워지고, 컨테이너 bind mount는 unlink된 inode를 계속 가리킨다 | 재시작 이전에 시작된 컨테이너의 mountinfo에 `…//deleted`가 보이고, 그 안의 mkdir는 ENOENT | **강하게 지지됨(아래)** |
| H2 | exec가 PID 1과 다른 mount namespace에서 돈다 | exec와 PID 1의 `/work` ino나 mnt ns가 다르다 | **배제(짧은 구간)**: 두 컨테이너 모두 mnt ns, ino, mountinfo가 같았다(`evidence/01`, `02`) |
| H3 | 같은 노드에서 다른 Pod를 삭제하면 그 정리 과정이 이 Pod의 emptyDir을 지운다 | 소유 Pod B를 삭제한 뒤 A의 mkdir가 실패한다 | **재현 안 됨**: B 삭제 후 60초 동안 6회 probe에서 모두 mkdir 성공, nlink=5(`evidence/02`). 원 run의 qualify Pod 삭제는 05:58 이후에도 있었지만 edgecore 재시작과 같은 시각은 아니다 |
| H4 | 외부 프로세스의 정리 작업이나 symlink 치환 | `/work`가 symlink이거나, 재시작과 상관없이 내용이 사라진다 | symlink 아님(lstat `islink=false`, realpath `/work`). 외부 정리 작업은 이번 증거로 배제할 수 없다. 다만 아래의 노드 공통 패턴은 edgecore 재시작 시점으로 설명된다 |
| H5 | dev0009 기본 runtime이 nvidia라서 생기는 문제 | runc를 쓰는 노드에서는 발생하지 않는다 | **기각**: runc를 쓰는 dev0008, dev0003에서도 같은 `//deleted` 패턴이 보였다 |

### H1 근거 (읽기 전용 host view, `evidence/03`, `05`)

- dev0009: 10-20 11:38에 시작한 flanneld의 ConfigMap mount 2개가 `kubernetes.io~configmap/{flannel-cfg,network-interfaces}//deleted` 상태다. edgecore 재시작 이후에 시작한 device plugin의 ConfigMap mount는 정상이다.
- dev0008: edgecore가 10-21 19:31에 시작했다. 그보다 먼저 시작한 flanneld도 똑같이 `//deleted`다.
- dev0003: edgecore 시작(starttime 124473908 tick) **이전에** 시작한 traefik, grafana, nanomq의 **emptyDir** mount가 모두 `kubernetes.io~empty-dir/…//deleted`다. **이후에** 시작한 컨테이너(app)의 emptyDir은 정상이다. dev0002와 dev0006에서는 emptyDir 컨테이너가 모두 edgecore 이후에 시작했고, 정상이다.
- 따라서 이 클러스터의 KubeEdge v1.23 edge 노드에서는 "edgecore 재시작 이전에 시작한 컨테이너의 emptyDir/ConfigMap 원본 디렉터리가 삭제된 상태"가 노드 공통 패턴으로 관측된다. 반례는 찾지 못했다.

### 메커니즘 재현 (`evidence/04`)

같은 이미지와 securityContext를 쓰는 소유 Pod 안에서 `/work` 아래 디렉터리로 chdir한 뒤 rmdir하고 `Path('control-receipts').mkdir(parents=True, exist_ok=True)`를 실행했다. 원 증상과 같은 이중 `FileNotFoundError`가 났다(dev0009 3/3, dev0008 1/1). 이때 `stat('.')`의 결과는 `nlink=0`, `is_dir=True`, listing `[]`였다. unlink된 디렉터리는 stat은 되지만 새 항목을 만들 수 없다.

## 판정

- **확인됨**: (1) source-e9가 살아 있던 구간에 dev0009 edgecore가 05:58:33에 재시작했다. (2) 같은 순간 실행 중 Pod의 ConfigMap 파일이 사라졌다. (3) edge 노드 4곳에서 edgecore 재시작 이전에 시작한 컨테이너의 emptyDir/ConfigMap 원본이 `//deleted` 상태다. (4) unlink된 디렉터리 안에서 mkdir하면 원 traceback과 같은 ENOENT가 재현된다.
- **가장 유력한 직접 원인(추론, 원 Pod mountinfo 미관측)**: 05:58:33 edgecore 재시작 때 source-e9의 emptyDir 호스트 디렉터리(`/var/lib/kubelet/pods/d4f764e3-…/volumes/kubernetes.io~empty-dir/work`)가 삭제되었다. netctl 컨테이너의 `/work`는 unlink된 디렉터리를 가리키게 되었고, 06:27 receipt mkdir가 ENOENT로 실패했다. 이 시점 이후 `/work/netctl-ready`도 사라졌을 것이다. 그러면 netctl readiness probe는 실패했어야 하는데, run에는 이 시점 이후의 Ready 상태를 기록한 증거가 없다.
- **미확인**: edgecore를 재시작한 주체와 원인(crash인지 수동 조작인지 다른 작업인지), 그리고 edged 안에서 삭제를 수행한 코드 경로(재시작 후 orphan/reconstruct 정리로 추정). 이를 확정하려면 dev0009 journal(`journalctl -u edgecore --since 05:58:00 --until 05:59:30`)이 필요하다. device plugin의 restart 12회/42h는 kubelet.sock 재생성이 반복되었을 가능성을 시사할 뿐이고, 재시작 횟수가 확인된 것은 아니다.

## 실험 영향과 남은 일 (구현하지 않음)

- 노드 edgecore가 재시작하면 그보다 오래 살아 있는 Pod의 `/work`가 조용히 무효화된다. 따라서 run 도중 edgecore 재시작을 감지하는 수단(예: 노드 edgecore starttime 비교, 또는 receipt 직전 `/work` nlink 확인)이나 Pod 재생성 정책이 필요하다. 설계 결정은 coordinator 몫이다.
- dev0003의 운영 Pod(traefik, grafana, nanomq)도 emptyDir이 `//deleted` 상태다. 새 파일 생성이 실패할 수 있다. 운영 리소스라서 건드리지 않았다.
- 재현에 쓴 명령: `.scratch/dev0009-workdir-diagnosis/README.md` 참조. 진단 Pod `diag-wd-e9-a`(UID 09a5e554-…), `diag-wd-e9-b`, `diag-wd-e8-a`(UID 46c85390-…)는 모두 삭제했다(`evidence/06`). edgecore나 runtime 재시작, 설치, 캐시 정리는 하지 않았다.
- 절차 메모: kubectl 호출은 모두 `rtk proxy`를 거쳤다. 로컬 파일 읽기와 python 계산 일부는 rtk 없이 직접 실행했다.
