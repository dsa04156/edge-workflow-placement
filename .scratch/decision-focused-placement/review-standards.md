# Standards 독립 검토

기준 HEAD: `43f9d110db4b85bd487214910653efbde481da83`. 새 디렉터리의 9개 텍스트 파일 전체와 실제 결과 JSON을 검토했다. 상위/루트 AGENTS, 루트/실험 README, CLAUDE 안내, docs/agents 계약, 구현 명세와 설계를 확인했다. 적용되는 하위 AGENTS·CONTRIBUTING·CODING_STANDARDS 및 GLOSSARY/ADR은 발견되지 않았다. 구현·테스트·기존 artifact는 수정하지 않았다.

## 문서화된 계약 불일치: 1건

**[P2] 쌍당 LP 조합 한도가 실제로는 경로마다 초기화된다.**
- 위치: `experiments/decision-focused-placement/regret.py:172`, `:138–145`; 근거: 같은 디렉터리 `README.md:30`의 “한 쌍당 활성 조합 수” 및 `regret.py:23`의 pairwise 한도 설명.
- 재현: `p=Placement("p", [([1],0),([0],10)])`, `q=Placement("q", [([1],1)])`, box `[(0,10)]`에서 `pairwise_regret_upper(p,q,box,combination_cap=3)`은 `upper=9, method="lp_exact"`를 반환한다. 경로마다 C(3,2)=3개, 한 쌍에서는 총 6개 조합을 처리하므로 문서의 쌍당 cap=3을 초과한다.
- 영향: 경로 수가 늘면 문서상 계산 예산보다 최대 경로 수 배의 열거가 가능하다. 별도 deadline은 있지만 이 cap의 계약을 보장하지 않는다. 이 재현은 regret 상한의 수학적 오류를 뜻하지 않는다.
- 최소 수정: 쌍 전체의 조합 수를 사전 계산해 cap 초과 시 simple 상한으로 반환하거나 잔여 cap을 경로 간 공유한다. 의도가 경로별 cap이라면 README와 상수 설명을 명시적으로 경로별로 정정하고 쌍 전체 비용을 별도로 설명한다.

## 판단에 따른 smell

수정을 요구할 만큼 구체적인 추가 smell은 발견하지 못했다. 작은 합성 fixture의 dict 구조나 수학 기호 이름만으로 추상화·리팩터링을 요구하지 않는다.

## 검증 및 한계

- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`: 33개 통과(0.115초).
- CLI 기본 설정 전체를 tempfile에 재실행: exit 0, 120/120 run, skipped 0. 모든 run의 planner 시간 외 필드, deterministic 결과, config와 fixture가 기존 JSON과 일치했다.
- 기존 결과 SHA256: `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6`.
- 합성/실측·proxy/종단 성능 경계와 음성 결과 보고는 적절하다. 실제 reviewer 모델 세부 ID는 독립 확인하지 못했으며 요청 모델을 실제 모델로 주장하지 않는다. Developer 모델·인증은 보고서의 증거 진술만 읽었고 재인증하지 않았다. Spec 축 전체 승인이나 클러스터 검증은 아니다.
