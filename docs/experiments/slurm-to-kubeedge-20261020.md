# Slurm 장비의 KubeEdge 이전 — 2026-10-20

## 범위와 상태

사용자가 지정한 두 장비에서 Slurm 제거, 호스트명 변경, 기존 KubeEdge 등록을 요청했다. 계정/비밀번호와 가입 토큰은 기록하지 않는다.

| 주소 | 확인한 장비 | 이전 상태 |
|---|---|---|
| 10.254.192.111 | Raspberry Pi 5 Model B Rev 1.1, 이전 slurm-w1 | etri-dev0008-raspi5로 변경, Slurm 패키지 제거, KubeEdge Ready, 비-hostNetwork Pod smoke 성공 |
| 10.254.192.128 | NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super, 이전 slurm-w2 | etri-dev0009-jetorn, Slurm 제거, KubeEdge Ready, 비-hostNetwork Pod smoke 성공 |

아래 연결 실패 기록은 복구 이전 이력이다. 최신 결과는 마지막 절을 따른다.

Orin은 직접 SSH 및 Pi 경유 접속으로 장비명과 관리자 권한을 확인했으나 연결이 간헐적으로 성공했다. 마지막 직접 접속은 연속 timeout이고 ICMP 3/3 미응답이다. 전원 고장, 중복 IP, 방화벽 또는 물리 네트워크 중 원인을 확정하지 않는다. 기존 controller 192.168.0.12는 접속되지 않아 controller 측 NodeName 제거는 수행하지 못했다.

## Raspberry Pi 수행 내용

- scontrol listpids가 No job steps exist를 반환함을 확인했다. 첫 준비 명령의 실패는 tar 입력 경로 부재 및 scontrol의 빈 작업 exit code였고, 각 경우 변경 전에 멈췄다.
- /root/slurm-migration-20261020 아래 root 전용 디렉터리에 /etc/slurm, /var/lib/slurm, hostname/hosts 등 백업. 패키지 제거 및 apt 로그도 해당 장비에 보존했다.
- slurmd/slurmctld disable/stop 후 설치된 Slurm 패키지를 apt-get remove로 제거했다. 과거 설정/결과를 purge하지 않았다. 검증 시 두 서비스 inactive, slurmd/slurmstepd 프로세스 없음.
- hostnamectl로 etri-dev0008-raspi5 지정, /etc/hosts 갱신.
- Debian containerd 1.7.24, runc, containernetworking-plugins 설치. systemd cgroup, /opt/cni/bin, overlay/br_netfilter, IPv4 forwarding 적용.
- 공식 v1.23.0 ARM64 keadm tar SHA256 cac2e5c72b24d9566df59d1884dfe4a838aa2ffcc3e76411597c2d56ea8f70a0 검증.
- Docker Hub/CDN reset으로 설치 이미지 다운로드 실패. 실패한 /etc/kubeedge는 failed-join-1/2로 보존했다. 같은 버전의 기존 Ready Pi dev0002 이미지 캐시를 ctr export/import하여 재사용했다. installation-package digest sha256:43cee661d264d8d85ac26840332ac53ba3c57206273445b4571d1875d9baea21.
- keadm join: cloudcore 10.254.192.47:10000, v1.23.0, systemd, unix:///run/containerd/containerd.sock, 새 노드명. 가입 토큰은 출력에서 제외했다.
- 기존 부팅 시 memory cgroup 비활성 확인. /boot/firmware/cmdline.txt 백업 후 cgroup_memory=1 cgroup_enable=memory 추가, 재부팅. /sys/fs/cgroup/cgroup.controllers에 memory가 생김을 확인.
- kube-system/kube-flannel-network-interfaces에 새 노드 eth0 항목만 추가했다. 새 노드 Flannel Pod만 재생성했다.
- 기존 정상 Pi의 설정과 대조해 edgecore metaServer/edgeStream 활성화, clusterDNS=169.254.96.16 적용. 기존 기본 비활성 metaServer가 Flannel API proxy 접속 실패를 유발했다.
- Debian containerd 기본 CNI 경로 /usr/lib/cni와 Flannel 설치 경로 /opt/cni/bin 불일치를 수정했다.

## 검증

Ready 확인 외에 migration-smoke-pi8-20261020 Pod를 명시적으로 새 노드에 실행했다. ghcr.io/flannel-io/flannel:v0.28.3의 sh로 MIGRATION_SMOKE_OK, hostname, eth0 주소를 출력하고 exit 0/Succeeded. Pod IP 10.244.10.3/24, MTU 1330. kubectl logs 조회 성공. 테스트 Pod 삭제 요청 후 잔여 확인은 아래 artifact 참조.

이는 컨테이너와 Pod IP 할당/로그의 smoke다. 모델, 가속기, DNS 응답, cross-node throughput 검증은 아니다. 신규 노드의 모든 기존 DaemonSet 이미지 준비를 완료했다는 뜻도 아니다.

## Orin 진행 경계와 재개

Orin에서 실제 GPU 모델 문자열을 읽었다. /usr/sbin/slurmstepd infinity만 보였으며 job cgroup 및 job spool 디렉터리는 없었다. containerd 2.2.1이 이미 설치돼 있었다. /etc/containerd/config.toml은 없었다. 기존 GPU 드라이버/모델 runtime은 변경하지 않았다.

/root/slurm-migration-20261020/slurm-config-state.tgz 생성 명령은 오류 없이 지나갔고 slurmd disable/stop 중 연결이 끊겼다. 이어지는 제거/hostnamectl 단계가 실행됐는지는 확인되지 않았다. 백업의 실제 파일/내용도 재접속 후 재확인한다. 마지막 hostname=slurm-w2, slurmd=deactivating, slurmctld=failed. 새 이름 etri-dev0009-jetorn은 예정값이며 완료값이 아니다.

재개 시 서비스 상태와 실행 중인 이전 shell/apt/dpkg를 먼저 확인한다. 같은 제거 명령을 바로 중복 실행하지 않는다. Slurm 중지가 끝났고 작업이 없음을 확인한 뒤 남은 제거/이름 변경을 마친다. 기존 containerd namespace/컨테이너/GPU runtime을 읽고 보존하며 신규 노드를 등록한다. Orin에는 아직 KubeEdge 설치나 Flannel 설정 추가를 하지 않았다.

## 근거

- artifacts/slurm-to-kubeedge-20261020/pi-node.json
- artifacts/slurm-to-kubeedge-20261020/pi-smoke.json
- artifacts/slurm-to-kubeedge-20261020/pi-system-pods.json
- https://kubeedge.io/docs/setup/install-with-keadm/
- https://github.com/raspberrypi/linux/issues/6980

Semantica 도구 검색이 unsupported call로 실패하여 영속 그래프에는 기록하지 못했다. 확인 결과는 이 문서에 보존한다.

## 사용자 복구 통보 후 재확인

10.254.192.129 SSH 호스트 키가 이전과 달라 StrictHostKeyChecking 검증이 실패했다. 현재 키는 로컬 known_hosts의 기존 10.254.192.128 키와 일치했다. 그 기존 키를 HostKeyAlias로 지정해 읽기 전용 확인한 결과 hostname=etri-ser0002-CGNMSB, architecture=x86_64, eno1=10.254.192.129/24였다. 현재 해당 주소는 Orin으로 취급할 수 없다. 이 장비에 Slurm 제거·이름 변경·가입을 수행하지 않았다. 10.254.192.128은 제한시간 내 SSH host key 응답이 없었다. Orin의 현재 주소 확인이 필요하다. 이전 Orin 작업의 완료 상태는 여전히 미확인이다.

## Orin 새 주소 확인

사용자가 10.254.192.128을 알려준 후 이전 .129에 신뢰했던 Orin SSH 키와 대조했다. StrictHostKeyChecking을 유지하고 접속하여 hostname=etri-dev0009-jetorn, 모델 NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super, enP8p1s0=10.254.192.128/24를 확인했다. 따라서 이전 hostname 변경 명령은 완료됐다. slurmd/slurmctld는 failed, edgecore inactive, containerd active였고 slurmstepd 잔여 프로세스가 있었다. 패키지 제거 완료 여부는 아직 확인하지 못했다.

그 후 직접 SSH 및 지속 연결의 명령 응답이 멈췄고, 정상 서버2(.129) 경유 TCP 연결도 timeout이었다. 새 주소는 확정됐으나 연결 안정성은 미해결이다. 이번 재개에서는 Orin의 추가 패키지 제거, 재부팅, containerd 변경 또는 KubeEdge 가입을 수행하지 않았다. known_hosts는 기존 신뢰 키를 보존한 백업과 성공한 host-key 검증을 근거로 서버2=.129, Orin=.128로 갱신했다.

## 연결 복구 후 이전 완료

2026-10-20 재접속에서 Orin=.128, server2=.129를 확인했다. Orin의 Slurm 패키지는 이미 제거돼 설정 파일만 남아 있었다. 작업 자식 프로세스가 없는 `/usr/sbin/slurmstepd infinity`에 TERM을 보냈고, 후속 조회에서 slurmstepd 부재를 확인했다. avahi-daemon 재시작으로 이전 호스트명 광고도 갱신했다. 기존 백업은 보존했다.

Orin의 containerd 2.2.1에 실행 중인 moby task가 없음을 확인했다. 부재하던 `/etc/containerd/config.toml`을 `containerd config default`로 생성하고 systemd cgroup을 활성화했다. 기존 `nvidia-ctk runtime configure --runtime=containerd --set-as-default`로 `/etc/containerd/conf.d/99-nvidia.toml`을 생성했다. 기존 NVIDIA 드라이버/OS는 교체하지 않았다. CNI 바이너리와 v1.23.0 가입 이미지 캐시는 기존 정상 ARM64 노드에서 전달했다.

`kube-flannel-network-interfaces`에 `etri-dev0009-jetorn: enP8p1s0`만 추가하고, keadm join에 기존 cloudcore 주소, 새 이름, v1.23.0, systemd 및 containerd socket을 지정했다. `--set=modules.metaManager.metaServer.enable=true`와 `--set=modules.edgeStream.enable=true`를 사용했다. 가입 중 임시 컨테이너 RemoveContainer timeout 경고가 있었으나 가입 명령은 exit 0으로 완료됐다. edgecore의 clusterDNS는 기존 정상 노드와 같은 169.254.96.16으로 설정했다.

노드 Ready, edgecore/containerd active, Flannel Running을 확인했다. `migration-smoke-orin9-20261020`은 비-hostNetwork Pod로 실행해 `MIGRATION_SMOKE_OK`를 출력하고 Succeeded/exit 0으로 종료했다. IP는 10.244.11.3/24, MTU 1330이고 kubectl logs도 성공했다. 근거는 `artifacts/slurm-to-kubeedge-20261020/orin-pod.json`, `orin-node.json`이다. 이 검증은 AI 모델 추론 또는 품질 동등성을 뜻하지 않는다.

기존 Nano용 GPU plugin 사양을 새 노드 전용 DaemonSet `nvidia-jetson-device-plugin-orin9`으로 복사했다. 기존 노드 selector는 변경하지 않았다. 공유 설정은 기존 tegra discovery 및 time-slicing replicas=2를 재사용하며, 두 논리 슬롯을 두 물리 GPU로 해석하지 않는다. 적용 명세는 `artifacts/slurm-to-kubeedge-20261020/orin-gpu-plugin.json`에 보존했다.

최종 확인에서 GPU plugin은 Running/Ready이고 `nvidia.com/gpu.shared=2`가 노드 allocatable에 등록됐다. 이미지 대기 후 호스트에서 동일 digest를 `ctr -n k8s.io images pull --platform linux/arm64`로 가져와 성공했다. 근거는 `orin-node-final.json`, `orin-system-pods-final.json`, `orin-gpu-plugin.log`다. smoke Pod는 삭제했다(`orin-cleanup.json`). GPU 자원 등록은 확인했으나 CUDA 모델 추론은 아직 검증하지 않았다.

보조 서비스의 잔여 문제: node-hardware-inventory는 기존 사설 레지스트리 192.168.0.56:5000 접속 timeout으로 이미지를 받지 못했다. edgemesh도 이미지 준비가 완료되지 않았다. 기본 Pod CNI와 GPU 자원 등록 성공을 모든 보조 서비스 정상으로 확대하지 않는다. Slurm controller 측 설정 삭제는 기존 controller 접근 불가로 미수행이다.
