# EdgeFlow Plan 1.0

**이종 엣지 AI 워크플로 배치 계획·비교 프로그램**

실측 작업 실행시간과 데이터 전달 비용, 실행 가능 노드와 자원 제약을 입력받아 DAG 전체의 배치를 계획하고 Kubernetes Pod 명세를 생성하는 Python 프로그램이다. 기존 검증 코드를 수정 없이 추출했다. 외부 Python 패키지 설치나 클러스터 연결 없이 제공 예제로 실행할 수 있다.

```bash
cd program-registration
python3 src/prepare.py --config examples/measured-compressed-2.json --policy topology_profile --run-id edgeflow-demo --out output-plan
python3 src/analyze.py examples/observed-attempts.jsonl --out output-comparison.json
python3 -m unittest discover -s src -p 'test_*.py'
```

`output-plan/`에 동결 입력 `profile-frozen.json`, 배치 `plan.json`, Pod 명세 `pods.json`이 생성된다. 기존 출력은 덮어쓰지 않는다. 다시 실행할 때 새 출력 이름을 지정한다. Pod 명세 생성까지가 이 패키지의 기능이며, 실제 모델 서비스·데이터 전달·배포는 별도 실행기가 필요하다.

완주 실험의 2Mbps 조건에서 서버 집중1271.25ms 대비 선택 배치917.44ms로 평균 지연27.83% 감소를 관측했다. 각6건의 단일 블록 비교다. greedy·HEFT도 같은 배치를 선택했으므로 정확 열거만의 우월성을 뜻하지 않는다. 자세한 전체 조건과 제한은 [성능 근거](docs/성능검증.md)에 기록했다.

- [등록 설명 초안](docs/프로그램등록설명서.md)
- [사용설명서](docs/사용설명서.md)
- [소스 출처 및 구성](docs/소스구성.md)
- [검증 결과](verification/validation.json)

이 폴더는 등록 준비용 소스·설명자료 묶음이다. 등록 접수나 등록 완료를 의미하지 않는다. 저작자·권리자·실제 창작일·공표일은 임의로 정하지 않았다.
