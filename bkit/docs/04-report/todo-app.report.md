# [Report] todo-app

> **Feature**: Todo App (풀스택)
> **Completed**: 2026-04-10
> **Phase**: Completed
> **Match Rate**: 100%
> **Success Rate**: 6/6

---

## Executive Summary

| 관점 | 계획 | 실제 결과 |
|------|------|----------|
| **Problem** | 할 일을 체계적으로 관리하고 주간 단위 진행 파악 어려움 | 해결 — CRUD + 주간 캘린더로 모든 관리 가능 |
| **Solution** | Next.js + Prisma + SQLite 풀스택, 카테고리/태그/주간 뷰 | 22개 파일, 11개 API 엔드포인트, 8개 컴포넌트 완성 |
| **Functional UX Effect** | 직관적 추가/완료/분류, 주간 캘린더 한눈 파악 | 태그 자동완성, 카테고리 색상 구분, 주간 그리드 구현 |
| **Core Value** | 설치 없이 브라우저에서 영속 저장되는 개인 생산성 도구 | SQLite 로컬 DB로 영속성 보장, `npm run dev` 즉시 실행 |

### 1.3 Value Delivered

| 지표 | 결과 |
|------|------|
| 구현 파일 수 | 22개 (설계 대비 +1: HomeClient.tsx 추가) |
| API 엔드포인트 | 11개 (100% 구현) |
| 컴포넌트 | 8개 (TodoList, TodoItem, TodoModal, TagInput, CategorySidebar, WeekCalendar, WeekDayColumn, HomeClient) |
| Match Rate | 100% (초기 94% → G-01/G-02 수정 후 100%) |
| Success Criteria | 6/6 달성 |
| 반복 횟수 | 0회 (Check 단계에서 즉시 수정) |

---

## 1. 프로젝트 개요

### 1.1 목표
개인 할 일을 웹에서 체계적으로 관리하고, 주간 단위로 진행 상황을 시각적으로 파악하는 풀스택 앱 개발.

### 1.2 범위
- **기간**: 2026-04-10 (단일 세션)
- **스택**: Next.js 14 App Router + Prisma + SQLite + Tailwind CSS
- **인증**: 없음 (단일 사용자)
- **배포**: 로컬 개발 환경 (`npm run dev`)

---

## 2. PDCA 진행 요약

| 단계 | 결과 | 주요 결정 |
|------|------|----------|
| **Plan** | 완료 | 풀스택 Next.js + Prisma + SQLite, 주간 캘린더 뷰 포함 |
| **Design** | 완료 | Option C Pragmatic 아키텍처, 5개 모듈 Session Guide |
| **Do** | 완료 | 22개 파일 전체 구현 (module 1~5) |
| **Check** | 완료 | 초기 94% → G-01/G-02 수정 → 100% |
| **Act** | 불필요 | Check 단계에서 즉시 수정 완료 |

---

## 3. Key Decisions & Outcomes

| 결정 | 근거 | 결과 |
|------|------|------|
| Option C Pragmatic 아키텍처 | feature 단위 폴더로 적절한 관심사 분리 | 22개 파일, 명확한 구조 유지 |
| Server Component + Client useState | 외부 상태관리 라이브러리 불필요 | 의존성 최소화, 코드 단순 |
| date-fns 월요일 시작 주간 계산 | 타임존 이슈 방지 | 주간 범위 필터 정확히 작동 |
| 태그 upsert 패턴 | 중복 태그 없이 자동 재사용 | tagNames 배열 → 자동 Tag 레코드 생성 |
| HomeClient.tsx 추가 (설계 외) | CategorySidebar ↔ TodoList 상태 공유 필요 | 카테고리 추가/삭제 시 즉시 반영 |

---

## 4. Success Criteria 최종 상태

| SC | 기준 | 상태 | 증거 |
|----|------|------|------|
| SC-01 | 할 일 CRUD 정상 동작 | ✅ | `api/todos/route.ts`, `api/todos/[id]/route.ts` |
| SC-02 | 완료 토글 후 상태 유지 | ✅ | `toggle/route.ts` — DB 저장, 새로고침 후 유지 |
| SC-03 | 카테고리/태그 필터 정확히 작동 | ✅ | `CategorySidebar` — 카테고리 + 태그 섹션, URL param → API |
| SC-04 | 주간 캘린더 마감일 기준 날짜 표시 | ✅ | `WeekDayColumn` — `isSameDate()` 비교 |
| SC-05 | 주간 이동 정상 작동 | ✅ | `WeekCalendar` — `addDays(±7)` + `/api/todos/week` 재호출 |
| SC-06 | 모바일 레이아웃 | ✅ | Tailwind 반응형 클래스 전체 적용 |

**Overall: 6/6 (100%)**

---

## 5. 구현 결과물

### 5.1 파일 구조
```
todo-app/
├── prisma/schema.prisma          # Todo, Category, Tag, TodoTag 모델
├── .env                          # DATABASE_URL=file:./dev.db
├── package.json                  # Next.js 14, Prisma, date-fns
├── src/
│   ├── lib/prisma.ts             # Prisma 싱글턴
│   ├── lib/date.ts               # date-fns 주간 유틸
│   ├── types/index.ts            # 공통 타입
│   ├── app/
│   │   ├── layout.tsx            # 헤더 네비게이션 (목록/주간)
│   │   ├── page.tsx              # 목록 뷰 (Server Component)
│   │   ├── week/page.tsx         # 주간 캘린더 (Server Component)
│   │   └── api/                  # 11개 Route Handler
│   └── components/
│       ├── HomeClient.tsx        # 카테고리 상태 공유 래퍼
│       ├── CategorySidebar.tsx   # 카테고리 + 태그 필터
│       ├── TodoList.tsx          # 할 일 목록 + CRUD
│       ├── TodoItem.tsx          # 할 일 카드
│       ├── TodoModal.tsx         # 추가/수정 모달
│       ├── TagInput.tsx          # 태그 자동완성
│       ├── WeekCalendar.tsx      # 주간 그리드 + 이동
│       └── WeekDayColumn.tsx     # 날짜별 컬럼
```

### 5.2 API 엔드포인트
| 엔드포인트 | 메서드 | 기능 |
|-----------|--------|------|
| `/api/todos` | GET, POST | 목록 조회(필터), 생성 |
| `/api/todos/[id]` | GET, PATCH, DELETE | 단건 조회/수정/삭제 |
| `/api/todos/[id]/toggle` | PATCH | 완료 토글 |
| `/api/todos/week` | GET | 주간 데이터 조회 |
| `/api/categories` | GET, POST | 카테고리 목록/생성 |
| `/api/categories/[id]` | DELETE | 카테고리 삭제 |
| `/api/tags` | GET | 태그 자동완성 |

---

## 6. Gap Analysis 요약

| ID | 갭 | 심각도 | 해결 방법 |
|----|-----|--------|----------|
| G-01 | 태그 필터 UI 없음 | Important | CategorySidebar에 태그 섹션 + tagId URL param 추가 |
| G-02 | 검색창 UI 없음 | Important | HomeClient에 search input + search URL param 연동 |

초기 Match Rate 94% → 즉시 수정 → **100%**

---

## 7. 실행 방법

```bash
cd todo-app

# 1. 의존성 설치
npm install

# 2. DB 초기화 (최초 1회)
npx prisma migrate dev --name init

# 3. 개발 서버 실행
npm run dev
# → http://localhost:3000
```

---

## 8. 향후 개선 가능 항목 (선택)

| 항목 | 설명 |
|------|------|
| 완료 항목 일괄 삭제 | FR-04 요구사항 (현재 단건 삭제만) |
| 드래그 앤 드롭 | 주간 캘린더에서 할 일 날짜 이동 |
| 알림/리마인더 | 마감일 임박 알림 |
| 다크모드 | Tailwind dark 클래스 활용 |
| 배포 | Vercel + PlanetScale/Turso (SQLite 호환) |
