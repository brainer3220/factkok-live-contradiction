# MagicPath 디자인 이식

인물 선택 메인은 기존 비교안 B(포트레이트)를 기준으로 이식했다. 상세는 승인된 2단 구성이다. 왼쪽에 영상과 현재 발언, 오른쪽에 주제·발언 기록·입장 변화·원문을 합쳤다.

| 원본 | 식별자 |
| --- | --- |
| 프로젝트 | `470371599212756992` · 팩트콕 · 출처로 비교하는 발언 |
| B 메인 | `470388113332641792` · `serene-breeze-1400` |
| B 기준 리비전 | `470388113332641793` |
| 기존 상세 | `470371602735972352` |
| 상세 기준 리비전 | `470382985808596992` |

MagicPath의 `get_component_code_context`와 `get_component_install_plan`으로 원본 파일과 의존성을 확인했다. B.css의 그리드·사진·검색 배치와 PeopleShared·Factkok.css의 상세 구성을 재사용했다. 호스트가 Python 정적 서버이므로 마크업과 CSS를 index.html·web/styles.css로 옮기고 React 상태를 web/app.js·model.mjs의 실제 API 수명주기로 연결했다. 별도의 React/Vite 런타임은 추가하지 않았다.

Linear 참고 토큰과 기존 시안의 검정·흰색·회색, 얇은 경계와 제한된 강조색을 사용한다. 샘플 숫자와 임의 판정은 이식하지 않았다. 준비 여부는 인물별 실제 파일 검증 결과, 비교 상태는 해당 run의 HTTP와 SSE 결과를 사용한다.

사진8개와 Noto Sans KR 전체 WOFF2는 시안 파일을 그대로 옮겼다. web/assets/portrait-sources.json에 사진별 원본 URL·저자·라이선스·변경 사항이 있고, 폰트 라이선스는 web/assets/fonts/OFL.txt에 있다. OFL 문구는 유지하며 말미 공백1곳만 정리했다. 브라우저에서 검증한 범위는 DEMO_VERIFICATION.md에 기록한다.
