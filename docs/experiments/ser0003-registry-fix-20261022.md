# ser0003 HTTP registry pull 수리 (2026-10-22)

대상 노드는 `etri-ser0003-cg0ms0` 하나뿐이다(10.254.70.81, arm64, containerd 2.2.1, kubelet 노드). 벤치마크, 프로파일링, 모델 추론은 하지 않았다. 근거 파일은 `.scratch/ser0003-registry-fix/`에 있다.

## 증상 (red)

- 실행 중인 run `wan-20261022t045727`의 `source-c3`와 `sink-c3`(두 컨테이너 모두 `IfNotPresent`)가 ErrImagePull / ImagePullBackOff 상태였다. 대상은 pinned 이미지
  `10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f`이다.
  - 정확한 오류: `Head "https://10.254.192.47:5000/v2/node-inventory-python/manifests/sha256:370a…a67f": http: server gave HTTP response to HTTPS client`
  - 근거: `evidence/before-exact-failed-messages.txt`, `before-run-events-*.txt`, `before-run-pod-*.json`
- 직접 소유한 Always probe(`probe-red-pod.json`)로 같은 오류를 05:02:26Z에 재현했다(`evidence/probe-red-events.txt`).

## 진단: dev0009와 원인이 다르다

host 파일은 기존 node-exporter Pod의 `/host/root`(읽기 전용)로 읽었다(`evidence/before-host-files.txt`, `before-config.toml`, `before-99-nvidia.toml`).

| 항목 | dev0009 | ser0003 |
|---|---|---|
| `config_path` | `/etc/containerd/certs.d:/etc/docker/certs.d` | 같음 |
| `use_local_image_pull` | false(기본, transfer service) | **true** (config.toml 47행, dump 확인) |
| colon으로 이은 경로 해석 | 단일 경로로 해석되어 실패 | `filepath.SplitList`로 분리되어 정상 |
| `certs.d/10.254.192.47:5000` | 없음 | **없음** |
| 기존 다른 hosts.toml | 없음 | `certs.d/192.168.0.56:5000` → `http://10.77.0.1:5000` |

- dev0009 docs-ref의 v2.2.1 소스 판독(`image_pull.go:491`)에 따르면 local pull 경로는 `config_path`를 분리한다. 같은 노드의 다른 workload 이벤트(`edgeai-inference-test/gpu-inference-test`)에서도 근거를 확인했다. `192.168.0.56:5000` 이미지가 `http://10.77.0.1:5000/...?ns=192.168.0.56%3A5000`로 요청됐으므로, 이 노드에서는 certs.d가 실제로 읽힌다.
- 따라서 ser0003에서는 **10.254.192.47:5000용 hosts.toml 추가만으로 동일 오류가 해소**됐다. config.toml 수정과 containerd 재시작은 필요하지 않다고 판단했다.
- `/etc/docker/certs.d`는 없다(`/etc/docker`는 빈 디렉터리이며 저장된 증거로 확인했다).

## 수리 (최소 변경)

- 추가한 파일은 `/etc/containerd/certs.d/10.254.192.47:5000/hosts.toml` 하나다(sha256 `4a760734c7c08d9267f6a9a79b55068fb45877ed27a477255af8b2f72b981aa5`). 내용은 dev0009, e8과 바이트 단위로 같다.
  ```toml
  server = "http://10.254.192.47:5000"
  [host."http://10.254.192.47:5000"]
    capabilities = ["pull", "resolve"]
  ```
- 실행 수단은 소유 임시 Pod `ser0003-registry-fix-maint`이다(`maint-pod.json`, 스크립트 원본 `add-hosts-toml.sh`).
  - 캐시된 `ghcr.io/flannel-io/flannel:v0.28.3`를 `Never`로 사용했고, hostPID + privileged + `nsenter -t 1` 구성이다.
  - guard: `containerd config dump`에서 `use_local_image_pull = true`와 colon `config_path`를 확인한다. 기대값이 아니면 중단한다. 대상 디렉터리가 이미 있으면 중단한다. 파일은 tmp에 쓴 뒤 mv로 교체했다.
- 전후 불변 항목(`evidence/maint-logs.txt`, `after-host-files.txt`)
  - `config.toml` sha256 `ec06ee76…9b7f`
  - `conf.d/99-nvidia.toml` sha256 `92feb337…71f8`
  - 기존 `certs.d/192.168.0.56:5000/hosts.toml` sha256 `17cf25ed…39fe`
  - dump 상 NVIDIA 설정: `default_runtime_name='runc'`, nvidia runtime `BinaryName='/usr/bin/nvidia-container-runtime'`
- containerd를 **재시작하지 않았다**. MainPID는 2153(active since 2026-10-07)으로 전후 같다. 백업할 대상이 없으므로 백업 파일은 만들지 않았다. 기존 파일을 변경하지 않았기 때문이다.
- 하지 않은 작업: 재부팅, 패키지 설치, 캐시 삭제, taint 및 다른 workload 변경, source collector 수정.

## 수용 결과 (green)

- `probe-pod.json`: 같은 pinned 이미지에 `imagePullPolicy: Always`를 적용해 python 실행을 확인했다. 결과는 `Succeeded`, exit 0이다.
  - 로그: `REGISTRY_IMAGE_OK aarch64 3.11.16`
  - 이벤트: `Pulling` → `Successfully pulled image "10.254.192.47:5000/…@sha256:370a…a67f" in 44ms` → Created/Started (`evidence/probe-events.txt`)
  - imageID는 `10.254.192.47:5000/node-inventory-python@sha256:370a…a67f`이다. image config `sha256:829cc065…b879`는 dev0009에서 레지스트리로 확인한 arm64 child config digest와 같다.
- 이벤트는 pull 성공에 44ms가 걸렸다고 기록한다. 같은 digest의 이미지가 기존 이름으로 node `status.images`에 있었으나, manifest 단독 시간과 실제 layer 전송량은 측정하지 않았다. 이번 증명은 "HTTP registry로 resolve/pull 경로가 성공함"이다. 전체 layer 재다운로드 성능을 측정한 것은 아니다.

## 노드 전후 비교 (`evidence/before-after-compare.txt`)

- Ready=True, lastTransition `2026-10-19T06:39:12Z`로 변화가 없다. allocatable(`nvidia.com/gpu: 1` 포함)도 같다. taint와 unschedulable은 수리 전후 모두 없다.
- 기존 Pod 16개의 ready, restartCount, containerID, state는 API 기준으로 같다.
- 다른 변화와 우리 작업의 관계
  - `edgeai-inference-test/gpu-inference-test`는 수리 전부터 ImagePullBackOff였고 이후 사라졌다. 우리는 이 Pod를 건드리지 않았다. 우리의 delete 대상은 소유 Pod 3개뿐이다(`probe-red-delete.txt`, `cleanup-delete.txt`). 이 Pod도 `10.254.192.47:5000/edgeai/gpu-inference`를 쓰므로 이 수리의 수혜 대상일 수 있다. 다만 확인하지 않았다.
  - run `wan-20261022t045727` Pod는 기존 collector가 05:01:47Z에 모두 정리했다(Killing 이벤트, `evidence/after-run-events.txt`). hosts.toml 기록(~05:02:35Z)보다 앞선 시점이다. 따라서 **해당 run의 c3는 수리 후 pull 기회를 얻지 못했다.** 새 벤치마크는 시작하지 않았다.
- 정리: `run-id=ser0003-registry-fix-20261022` 잔여 Pod는 0개다(`evidence/cleanup-check.txt`).

## 재사용 및 원복

- 읽기 전용 진단: `.scratch/ser0003-registry-fix/diagnose.sh <node> [registry]`. 이 스크립트는 `use_local_image_pull`, `config_path`, 그리고 해당 registry의 hosts.toml 존재 여부를 출력한다.
- 원복: host의 `/etc/containerd/certs.d/10.254.192.47:5000/` 디렉터리를 제거한다. containerd 재시작은 필요하지 않다.

## 판정 레벨

- **실제 운영 노드 설정 변경**: 완료했다.
- **같은 pinned 이미지의 Always pull과 python 실행**: 성공을 관측했다.
- **실험 run의 c3 성공**: 미관측이다. 해당 run은 수리 전에 정리됐다.
- 다른 노드는 점검하지 않았다.
