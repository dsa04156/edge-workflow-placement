# edgecore crash / restart-teardown fix (task_504a17aa18ae)

결론·배포 계획은 `docs/experiments/edgecore-crash-fix.md`, 1차 출처 표는 `research-findings.md`에 있다. 호스트 변경은 하지 않았다.

- `src/kubeedge/`: 공식 `github.com/kubeedge/kubeedge` clone. branch `local/v1.23.0-edgecore-crash-fix` = v1.23.0 + 2 commits(로컬 전용, push하지 않음)
- `src/kubeedge-v1.23.1/`: 같은 2 commits를 v1.23.1에 적용한 worktree(`local/v1.23.1-edgecore-crash-fix`)
- `patches-v1.23.0/0001,0002*.patch` + `SHA256SUMS`: 배포 대상 패치. (`patches/`의 두 파일은 범위를 잘못 지정해 export한 무관한 upstream 커밋이다. 사용하지 말 것. 삭제는 권한 확인 대상이라 남겨 두었다)
- `pr6836.diff`: upstream PR #6836 원본 diff(0001과 동일 내용)
- `official-v1.23.0/`: 공식 release tarball과 checksum(대조용)
- `build/`: arm64 바이너리 3종과 `SHA256SUMS`
- `host/edgecore-fix.sh`(preflight/install/verify/rollback), `host/exec_stress.sh`
- `evidence/01..08`: 테스트와 빌드 원본 출력

## 빌드 명령 (실행한 그대로)
```sh
cd src/kubeedge && export GOWORK=off GOFLAGS=-mod=vendor && source hack/lib/init.sh
GOARCH=arm64 GOOS=linux CGO_ENABLED=1 CC=aarch64-linux-gnu-gcc go build -tags netgo,osusergo \
  -o ../../build/edgecore-v1.23.0-2-g389862263-arm64-static \
  -ldflags "$(kubeedge::version::ldflags) -extldflags -static" ./edge/cmd/edgecore
# dynamic 판: -tags와 -extldflags -static 제외(GLIBC_2.34 이상 필요)
```

## r1 개정 (task_a228ddcf8c95) — 초판 산출물은 REJECTED, 보존만 함

- `src/kubeedge-r1/`: worktree `local/v1.23.0-edgecore-crash-fix-r1` = v1.23.0 + `ffd81d3a7`(P1 ReadChan guard) + `da0ee076a`(P2 sources-ready 복원 + pre-list 보관)
- `patches-r1-v1.23.0/` + `SHA256SUMS`: 현재 후보 패치. 초판 `patches-v1.23.0/`, `build/`, `host/`, `evidence/`는 REJECTED이며 수정하지 않았다
- `build-r1/edgecore-v1.23.0-2-gda0ee076a-arm64`(sha256 `afda505d…cc6e`), `LDFLAGS.txt`. 모든 테스트가 통과한 뒤 빌드했다
- `host-r1/`: 개정 host 스크립트. `edgecore --version` 금지(데몬 기동)
- `evidence-r1/01..08`: P1 red(v1.23.0, #6836) / green(-race), pkg/stream race 비교, P2 red(v1.23.0, fork-only) / green(-race)
- `upstream-ref/`: upstream kubernetes v1.32.10과 kubeedge fork v1.32.10-kubeedge1의 `pkg/kubelet/config/config.go`(sha256은 보고서에 기록)

빌드(r1):
```sh
cd src/kubeedge-r1 && export GOWORK=off GOFLAGS=-mod=vendor && source hack/lib/init.sh
GOARCH=arm64 GOOS=linux CGO_ENABLED=1 CC=aarch64-linux-gnu-gcc go build \
  -o ../../build-r1/edgecore-v1.23.0-2-gda0ee076a-arm64 -ldflags "$(kubeedge::version::ldflags)" ./edge/cmd/edgecore
go test -count=1 -race ./edge/pkg/edgestream/ ./edge/pkg/edged/ && go test -count=1 ./pkg/stream/
```
