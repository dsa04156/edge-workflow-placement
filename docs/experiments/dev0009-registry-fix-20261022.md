# dev0009 HTTP registry pull 수리 (2026-10-22)

대상은 `etri-dev0009-jetorn`(10.254.192.128, Jetson Orin, containerd 2.2.1) 한 노드뿐이다. 모델 추론, 프로파일링, 벤치마크는 하지 않았다.

## 증상 (red)

기존 probe `.scratch/ready-node-expansion/registry-check-20261022t042941`에서 같은 pinned 이미지
`10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f`를
`imagePullPolicy: Always`로 받게 했다. e8은 성공했고 e9은 다음 오류로 실패했다.
`Head "https://10.254.192.47:5000/...": http: server gave HTTP response to HTTPS client`

## 진단

1. 기존 node-exporter Pod(`/host/root` 읽기 전용)로 읽었다. dev0009의 `/etc/containerd/config.toml`(version 3)에는
   `[plugins."io.containerd.cri.v1.images".registry] config_path = "/etc/containerd/certs.d:/etc/docker/certs.d"`가 있었다.
   작업자는 `certs.d`와 `/etc/docker/certs.d`가 없었다고 보고했다. Docker 디렉터리 부재는 저장된 증거에서 독립 재검증하지 못했다. 정상인 e8(containerd 1.7.24)에는 `certs.d/10.254.192.47:5000/hosts.toml`(http, pull/resolve)이 있었다.
2. 1차 수리로 hosts.toml만 추가했다. 공식 문서 `docs/hosts.md`(v2.2.1)는 이 디렉터리 변경에 재시작이 필요 없다고 한다.
   그러나 Always-pull probe1은 **같은 HTTPS 오류로 실패**했다(`evidence/probe1-hoststoml-only-final.json`).
   PID 시작 시점만으로 설정 적용 여부를 배제하지 않는다. 저장된 증거만으로 이 시점 비교를 재검증하기는 어렵다.
3. 근본 원인은 v2.2.1 소스에서 찾았다(`docs-ref/` 보존).
   - 기본 경로 `use_local_image_pull=false`에서 CRI pull은 transfer service를 사용한다.
   - 이 경로는 `registry.WithHostDir(c.config.Registry.ConfigPath)` → `config.HostDirFromRoot(hostDir)`를 호출하며, 문자열을 나누지 않는다(`internal/cri/server/images/image_pull.go:312`, `core/transfer/registry/registry.go:127-128`).
   - `filepath.SplitList`는 local pull 경로(`image_pull.go:491`)에서만 쓰인다.
   - 결과적으로 `:`로 이은 두 디렉터리 값이 존재하지 않는 단일 경로로 해석된다. hosts.toml을 찾지 못하고 기본 HTTPS로 간다.
   - 이는 소스 판독에 근거한 해석이다. upstream issue 대조는 하지 않았다.

## 수리 (최소 변경)

- 추가: `/etc/containerd/certs.d/10.254.192.47:5000/hosts.toml`(sha256 `4a760734…81aa5`). 내용은 e8과 같다.
  ```toml
  server = "http://10.254.192.47:5000"
  [host."http://10.254.192.47:5000"]
    capabilities = ["pull", "resolve"]
  ```
- 수정: `/etc/containerd/config.toml` 한 줄. 백업은 `/etc/containerd/config.toml.bak-dev0009-registry-fix-20261022`(sha256 `27b61697…32c4`, 원본과 동일)이다.
  ```diff
       [plugins."io.containerd.cri.v1.images".registry]
  -      config_path = "/etc/containerd/certs.d:/etc/docker/certs.d"
  +      config_path = "/etc/containerd/certs.d"
  ```
  수정 후 sha256은 `17b4a5d6…d9e4`이다. 작업자의 Docker 디렉터리 부재 보고를 근거로 단일 경로를 선택했다. 해당 부재의 저장 증거는 부족하다.
- 확인 결과 다음은 변경되지 않았다.
  - `conf.d/99-nvidia.toml` sha256 `5f0ed2d5…9eaa`(전후 동일)
  - `containerd config dump` 상의 `default_runtime_name='nvidia'`, `BinaryName='/usr/bin/nvidia-container-runtime'`
  - TLS 전역 설정, transfer plugin 설정
- `systemctl restart containerd`만 실행했다(PID 251229→1237541, active). edgecore는 active이며 재시작하지 않았다. 노드 재부팅, 패키지 설치, 이미지 캐시 삭제는 없었다.
- 실행 수단은 자체 소유 임시 Pod이다. 둘 다 캐시된 `ghcr.io/flannel-io/flannel:v0.28.3`, `imagePullPolicy: Never`를 사용했고 namespace는 `topology-profile-lab`이다.
  - `maintenance-pod.yaml`: hostPath `/etc/containerd/certs.d` 하나만 마운트. 파일이 이미 있으면 중단한다.
  - `maintenance2-pod.json` / `fix-config-path.sh`: `hostPID` + privileged, `nsenter -t 1` 사용. 다음 guard를 통과해야 진행한다.
    - 원본 sha 일치
    - 대상 줄 유일
    - 기존 백업은 sha를 비교 검증. 새 백업은 sha를 출력하며, 원본과의 일치는 사후 로그 대조로 확인
    - `containerd config dump` 파싱과 유효값 확인. 실패하면 원복하고 재시작하지 않는다.
- 첫 maint2 실행은 검증 grep 오류로 중단됐다(dump가 작은따옴표 출력). 따옴표 문제로 guard가 원복했고 재시작하지 않았다. 원복 sha는 `27b61697…`로 확인했다(`evidence/maint2-attempt1-aborted-logs.txt`, `after-attempt1-host-files.txt`). guard를 수정한 뒤 재실행해 성공했다.

## 수용 결과 (green)

- probe2(`probe-pod.json`)는 기존 e9 probe와 같은 spec이고, 이름만 다르며 Always이다. 결과는 `Succeeded`, exit 0, 로그 `REGISTRY_IMAGE_OK aarch64`이다.
- 실제 imageID는 `10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f`이다.
  - image config는 `sha256:829cc065f506a60d332260af9fc48c590d60cfeff8bfc20df57b7baf20edb879`이다. 레지스트리에서 받은 arm64 child manifest의 config digest와 일치한다.
- 레지스트리 재확인 결과(`evidence/registry-*`)는 다음과 같다.
  - index GET 200, `Docker-Content-Digest: sha256:370a8339…a67f`, 본문 sha256 일치
  - arm64 child `sha256:8b29ec24…3c57`, 헤더와 본문 일치
- 노드 상태는 수리 전후 같다.
  - Ready=True(lastTransition 2026-10-21T09:56:39Z, 변화 없음)
  - `nvidia.com/gpu.shared=2` 유지, taint 없음
- 기존 Pod 6개의 ready, restartCount, containerID, startedAt은 API 기준으로 전후 동일하다(`evidence/before-after-container-compare.txt`).
  - 단, KubeEdge 상태 보고 지연 가능성이 있어 "무영향"을 증명한 것은 아니다.
  - API의 컨테이너 식별자와 상태가 같다는 관측이며, shim 유지 자체를 직접 관측한 것은 아니다.
- 정리: 자체 Pod 3종(maint, maint2, probe)은 모두 삭제했다. `run-id=dev0009-registry-fix-20261022` 잔여 Pod는 0개이다.

## 남은 문제 (범위 밖, 미변경)

- `kube-system/node-hardware-inventory-tlpdn`과 `kubeedge/edgemesh-agent-j89sz`는 여전히 ImagePullBackOff이다. 둘 다 폐기된 `192.168.0.56:5000` 이미지를 참조한다(`evidence/before-preexisting-pullbackoff.txt`). 이번 수리 대상이 아니다.
- 다른 containerd 2.x 노드에서 colon 구분 `config_path`를 쓰면 같은 문제가 생길 수 있다. 다른 노드는 점검하지 않았다.
- 백업 파일은 host에 남겨 두었다. 원복 절차는 백업을 `config.toml`로 복사한 뒤 containerd를 재시작하는 것이다.

## 근거 위치

`.scratch/dev0009-registry-fix/`에 있다. `evidence/`(before/after node/pods JSON, maint/probe 로그와 최종 JSON, registry 헤더, host 파일 hash), `docs-ref/`(containerd v2.2.1 공식 docs/소스 사본), manifest와 스크립트가 들어 있다.

## 독립 리뷰와 최종 확인

Claude Opus 5.5 구현과 Codex gpt-6-astra 독립 리뷰의 실제 모델을 세션 기록에서 확인했다. 리뷰 판정은 복구 수용 가능이며, 증거 범위와 스크립트 검증 설명의 경미한 보완 3건을 위에 반영했다. NVIDIA 값은 스크립트의 자동 assertion이 아니라 출력 및 전후 hash 대조로 확인했다. coordinator의 최종 API 재조회도 Ready=True, gpu.shared=2, 자체 Pod 0개였다(`evidence/coordinator-final-validation.json`).
