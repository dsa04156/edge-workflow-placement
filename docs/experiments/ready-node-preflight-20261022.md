# 9-node helper/guard preflight — 2026-10-22

사용자의 실행 요청으로 확장9노드의 실제 helper 준비와 guard on/off 1회를 계획했다. 모델 inference 0회. 원본은 `artifacts/topology-profile-live/wan-20261022t042048`이며 기존 수집 클래스의 topology_preflight → deploy → guard(True) → guard(False) → finally close를 호출했다. 수집기 소스는 변경하지 않았다.

## 결과

- 노드 Ready/cordon/taint 사전 검사 통과. NotReady dev0001/dev0004/dev0007 제외.
- 7노드(e0/e1/e6/c/c1/c3/c4)의 helper 준비 확인.
- dev0008/dev0009의 source helper는 ImagePullBackOff. pinned image `192.168.0.56:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f` manifest HEAD 요청에서 `dial tcp 192.168.0.56:5000: i/o timeout` 확인. Ready가 이미지 실행 가능성을 뜻하지 않는 사례다.
- `008-helpers-ready` 제한시간으로 실패. guard phase에 미도달하여 과거 exec EOF의 해결·재현 여부는 미검증. 모델·프로파일·정책 성능 비교도 수행하지 않았다.
- docs/experiments/slurm-to-kubeedge-20261020.md에도 신규 노드의 같은 private registry timeout이 미해결로 기록돼 있다. 저장소 프로토콜/라우팅의 근본 원인을 추가 확인하지 못했다.
- 두 노드에 현재 계정 jinuk로 BatchMode/StrictHostKeyChecking을 유지한 읽기 전용 SSH hostname 접속을 시도했으나 Permission denied(publickey,password). 기존 관리자 접속 방법을 사용자에게 요청했다. 비밀번호를 파일이나 보고서에 저장하지 않았다.

## 정리

기본 close는 준비되지 않은 source-e8 삭제 대기에서 시간 초과하여 remaining_owned=1을 기록했다. 이후 같은 run label·deletionTimestamp·Pending 및 두 컨테이너 모두 containerID 없음/waiting을 확인한 뒤 해당 Pod API 객체만 force delete했다. 후속 조회에서 이 run의 Pod/ConfigMap API 객체0개. 노드의 sandbox 해제는 SSH 접근이 없어 독립 검증하지 못했다. 원래 cleanup.json은 덮어쓰지 않았고 cleanup-followup.json에 별도 증거를 남겼다.

보호 workload208개 중2개의 Ready 상태가 전후 False→True로 바뀌었다. 이들의 설정은 변경하지 않았으며 인과관계를 단정하지 않는다.

다음 단계는 기존 관리자 경로를 확보해 이미지 캐시/레지스트리 접근을 확인하는 것이다. 동일 digest의 기존 캐시 전달 가능성을 검토하되 이미지 교체·노드 재부팅·taint 제거·cloud tunnel 활성화는 수행하지 않았다. 준비되지 않은 두 노드를 몰래 제외한7노드 성능 결과를 만들지 않았다.

## 현행 레지스트리 직접 확인 및 주소 수정

후속 사용자 지시에 따라 live Pod image 참조를 조회했다. 현행 저장소는 **10.254.192.47:5000**이며 edgeai/cloudcore/resource-advisor 등에서 사용 중이다. HTTP /v2/와 node-inventory-python의 기존 digest manifest 모두200. 응답 Docker-Content-Digest와 실제 manifest SHA256이 기존370a8339…a67f와 일치하며 amd64/arm64 manifest가 포함된다. 먼저 이 조회를 하지 않고 주소를 사용자에게 물었던 점을 바로잡았다.

run_smoke.py의 IMAGE 호스트만 새 주소로 변경했다. 기존 digest 유지, 과거 artifact 불변. 운영 Deployment/DaemonSet에 남은 다른 구주소 참조는 이번 범위에서 변경하지 않았다.

imagePullPolicy=Always로 두 노드에 짧은 Python probe를 실행했다. dev0008은 `REGISTRY_IMAGE_OK aarch64`, Succeeded/exit0. dev0009는 새 주소에 도달했으나 `http: server gave HTTP response to HTTPS client`로 ErrImagePull. 따라서 dev0009의 HTTP registry 설정은 추가 확인이 필요하다. 이 결과는 AI 모델 qualification이 아니다. probe Pod2개 삭제 및 잔여0 확인. 전체 로컬 unit88개 통과.

증거: `.scratch/ready-node-expansion/registry-check-20261022t042941/`의 registry-evidence.json, manifest.json, history.json, last-pods.json, logs, summary.json. 기존 관리자 SSH 정보 요청은 저장소 주소 확인에는 불필요했지만, dev0009의 호스트 containerd 설정 변경 경로는 아직 확보하지 못했다.
