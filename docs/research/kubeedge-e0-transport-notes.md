# KubeEdge E0: Task 사이 HTTP tensor 전달 메모

조사일: 2026-10-16. Context7과 GitHub MCP로 공식 문서·예제를 확인했다. 이번 조사는 클러스터 접속·설정 변경·Pod exec·명령 실행·실제 전송을 수행하지 않았다. 문서의 기능과 현재 클러스터의 통과 여부는 별개다.

## 공식 자료에서 명시한 경계

- EdgeMesh는 Service discovery·traffic proxy를 제공한다. DNS는 Service ClusterIP를 해석하고 proxy/load balancer가 backend로 전달한다. cross-LAN은 direct tunnel 또는 relay 경로를 사용할 수 있다. [공식 README](https://github.com/kubeedge/edgemesh/blob/main/README.md)
- 공식 HTTP 예제는 `hostname-svc:12345`, namespace 사이 예제는 `tcp-echo-edge-svc.edgezone:2701`을 사용한다. Service의 `port`와 실제 worker의 `targetPort`를 구분한다. HTTP 예제의 포트 이름은 `http-0`, 전송 protocol은 TCP다. [공식 test case](https://github.com/kubeedge/edgemesh/blob/main/docs/guide/test-case.md), [manifest](https://github.com/kubeedge/edgemesh/blob/main/examples/hostname.yaml)
- Edge CNI는 별도 기능이며 공식 guide는 기본 비활성이라고 명시한다. 미활성 시 PodIP 연결은 기존 CNI에 의존한다. 따라서 Service 연결 성공만으로 직접 PodIP cross-subnet 연결까지 보장할 수 없다. [공식 CNI guide](https://github.com/kubeedge/edgemesh/blob/main/docs/guide/cni.md)
- `kubectl logs/exec`는 KubeEdge의 CloudStream/EdgeStream tunnel 경로다. 요청 데이터가 다니는 Service/EdgeMesh 경로와 구별한다. 로그 접근 실패만으로 worker의 HTTP 실패를 단정할 수 없다. [KubeEdge debug 문서](https://kubeedge.io/docs/advanced/debug/)

## 신규 진단 worker에서의 적용 제안 — 아직 미검증

1. 기존 `scheduler-research` 안의 Service 이름·namespace·포트로 `/health`를 호출하고 응답에 worker Pod/node identity를 넣어 실제 수신 대상을 확인한다. 이름 해석, 연결, HTTP status, payload 무결성을 각각 기록한다.
2. 동일 namespace 단축 이름과 `service.namespace`를 우선 구분하고, `cluster.local`은 실제 clusterDomain 확인 뒤 FQDN에 사용한다. Service DNS 실패·ClusterIP 실패·PodIP 실패를 서로 다른 진단 항목으로 둔다.
3. 원본 텐서 bytes·dtype·shape·byte order·request ID·SHA256를 보내고 수신 측 hash와 비교한다. JSON/base64를 사용하면 원본 텐서 크기와 wire payload 크기를 함께 남긴다. HTTP 성공만으로 텐서 동일성을 인정하지 않는다.
4. Service는 한 개의 의도한 backend를 선택하게 구성하거나 응답 identity를 반드시 검증한다. 여러 endpoint의 load balancing을 특정 노드 간 측정으로 오인하지 않는다. 신규 진단 리소스 외 기존 설정을 변경하는 것은 이 제안에 포함하지 않는다.

## 측정 이름과 타이머

| 측정 | 의미·제약 |
|---|---|
| `http_roundtrip_ms` | 클라이언트에서 요청 시작 전부터 응답 body 전체 `read()` 완료까지. DNS·연결·업로드·서버 대기/처리·다운로드가 포함되는 **응용 RTT**다. 순수 전송시간·단방향 지연과 다르다. [urllib 동작](https://docs.python.org/3.11/library/urllib.request.html#urllib.request.urlopen) |
| `receive_body_ms`, `infer_ms` | 서버 프로세스 안에서 각각 body 읽기와 inference 구간의 차이. handler가 호출되기 전의 queue/connection 지연은 포함하지 않으므로 RTT에서 무조건 빼도 network-only 시간이 되지 않는다. [HTTPServer/handler](https://docs.python.org/3.11/library/http.server.html) |
| `payload_bytes/http_roundtrip` | 필요하면 **응용 유효 처리율**로만 표기한다. TCP 대역폭 또는 one-way link bandwidth의 추정치라고 부르지 않는다. serialization·응답 크기·서버 계산의 영향을 받는다는 해석이다. |
| local elapsed time | 같은 프로세스에서 `perf_counter_ns()`의 전후 차이를 사용한다. 서로 다른 host의 counter 절대값을 빼서 one-way 시간을 만들지 않는다. 기준점은 정의되지 않는다. [Python timer 계약](https://docs.python.org/3.11/library/time.html#time.perf_counter) |

- Python 3.11 `urllib.request`는 HTTP/1.1 요청에 `Connection: close`를 포함한다고 명시한다. 매 호출을 persistent-connection 측정으로 해석하지 않는다. 환경 proxy 자동 적용도 있으므로 내부 Service 요청이 의도한 경로인지 확인한다. [urllib 공식 문서](https://docs.python.org/3.11/library/urllib.request.html)
- `HTTPServer`는 기본 진단에 사용할 수 있으나 직렬 처리가 queue를 만들 수 있다. `ThreadingHTTPServer`를 선택하면 동시 요청 처리와 ORT thread 경쟁 조건을 함께 고정한다. handler에서 body/response 길이·timeout·error 응답을 명확히 둔다. [Python server 계약](https://docs.python.org/3.11/library/http.server.html)
- 첫 요청과 후속 요청을 구분하고, payload 고정·반복 횟수·응답 크기·모델 warm 상태를 기록한다. 양방향을 각각 측정하며 비대칭을 허용한다. 이 단계의 결과는 HTTP tensor 전달 가능성 및 응용 RTT이며, 전송비용 모델의 충분성 검증은 후속 과제다.

현재 클러스터의 Service DNS·EdgeMesh tunnel·직접 PodIP·로그 접근·tensor hash 일치는 **모두 이 조사에서 미검증**이다.
