# [Plan] todo-app

> **Feature**: Todo App (풀스택)
> **Created**: 2026-04-10
> **Phase**: Plan
> **Status**: Draft

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 할 일을 체계적으로 관리하고 주간 단위로 진행 상황을 파악하기 어렵다 |
| **Solution** | Next.js + Prisma + SQLite 기반 풀스택 To-do 앱으로 CRUD, 카테고리/태그 분류, 주간 캘린더 뷰 제공 |
| **Functional UX Effect** | 할 일 추가/완료/분류가 직관적이고, 주간 캘린더에서 한눈에 스케줄 파악 가능 |
| **Core Value** | 설치 없이 브라우저에서 바로 사용 가능하고 데이터가 영속적으로 보존되는 개인 생산성 도구 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 개인 할 일 관리의 불편함 해소 — 주간 단위 진행 관리 필요 |
| **WHO** | 개인 사용자 (인증 없음, 단일 사용자 기준) |
| **RISK** | SQLite 동시성 제한 (단일 사용자이므로 허용), 주간 뷰 날짜 계산 복잡도 |
| **SUCCESS** | 할 일 CRUD 정상 동작, 카테고리/태그 필터, 주간 캘린더 뷰에서 마감일별 표시 |
| **SCOPE** | Next.js App Router + Prisma ORM + SQLite, 인증 없음, UI 라이브러리 자유 선택 |

---

## 1. 개요

### 1.1 배경 및 목적
개인 할 일을 웹에서 쉽게 관리하고 주간 단위로 진행 상황을 파악하는 도구가 필요하다. 브라우저 기반으로 접근이 쉽고, 데이터가 영속적으로 저장되어야 한다.

### 1.2 범위 (Scope)
- Next.js 14+ App Router 기반 풀스택 앱
- Prisma ORM + SQLite 로컬 데이터베이스
- 인증 없음 (단일 사용자)
- 반응형 웹 UI

---

## 2. 요구사항

### 2.1 기능 요구사항 (Functional Requirements)

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| FR-01 | 할 일 추가 (제목, 설명, 마감일, 카테고리, 태그) | Must |
| FR-02 | 할 일 목록 조회 (전체 / 카테고리별 / 태그별 필터) | Must |
| FR-03 | 할 일 수정 (모든 필드 수정 가능) | Must |
| FR-04 | 할 일 삭제 (단건 / 완료된 항목 일괄 삭제) | Must |
| FR-05 | 완료 체크 토글 (완료/미완료 상태 전환) | Must |
| FR-06 | 카테고리 관리 (생성, 조회, 삭제) | Should |
| FR-07 | 태그 관리 (자유 입력, 자동완성) | Should |
| FR-08 | 주간 캘린더 뷰 (이번 주 월~일 그리드, 마감일 기준 배치) | Must |
| FR-09 | 주간 이동 (이전 주 / 다음 주 네비게이션) | Must |
| FR-10 | 데이터 영속성 (SQLite 저장, 새로고침 후 유지) | Must |

### 2.2 비기능 요구사항 (Non-Functional Requirements)

| ID | 요구사항 | 기준 |
|----|----------|------|
| NFR-01 | 페이지 로드 속도 | 초기 로드 2초 이내 |
| NFR-02 | API 응답 속도 | CRUD 작업 500ms 이내 |
| NFR-03 | 반응형 지원 | 모바일(375px) ~ 데스크탑(1440px) |
| NFR-04 | 데이터 무결성 | Prisma 스키마 레벨 검증 |

---

## 3. 데이터 모델 (개요)

```
Todo
├── id: Int (PK)
├── title: String
├── description: String?
├── dueDate: DateTime?
├── completed: Boolean (default: false)
├── createdAt: DateTime
├── updatedAt: DateTime
├── categoryId: Int? (FK → Category)
└── tags: Tag[] (M:N)

Category
├── id: Int (PK)
├── name: String (unique)
└── color: String?

Tag
├── id: Int (PK)
└── name: String (unique)
```

---

## 4. 주요 화면 구성

| 화면 | 설명 |
|------|------|
| **목록 뷰** | 전체 할 일 목록, 카테고리/태그 사이드바 필터, 완료 토글 |
| **주간 캘린더 뷰** | 7일 그리드, 날짜별 할 일 카드, 주간 이동 버튼 |
| **할 일 추가/수정 모달** | 제목, 설명, 마감일, 카테고리, 태그 입력 폼 |

---

## 5. 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Next.js 14 (App Router), React, TypeScript |
| Styling | Tailwind CSS |
| Backend | Next.js Route Handlers (API) |
| ORM | Prisma |
| DB | SQLite |
| 상태 관리 | React useState / useReducer (서버 상태: fetch + revalidate) |

---

## 6. API 엔드포인트 (개요)

| Method | Endpoint | 기능 |
|--------|----------|------|
| GET | `/api/todos` | 목록 조회 (필터 쿼리 지원) |
| POST | `/api/todos` | 할 일 생성 |
| GET | `/api/todos/[id]` | 단건 조회 |
| PATCH | `/api/todos/[id]` | 수정 |
| DELETE | `/api/todos/[id]` | 삭제 |
| PATCH | `/api/todos/[id]/toggle` | 완료 토글 |
| GET | `/api/todos/week` | 주간 데이터 조회 |
| GET | `/api/categories` | 카테고리 목록 |
| POST | `/api/categories` | 카테고리 생성 |
| DELETE | `/api/categories/[id]` | 카테고리 삭제 |
| GET | `/api/tags` | 태그 목록 (자동완성용) |

---

## 7. 구현 순서 (권장)

1. **Phase 1 — 프로젝트 초기화**
   - `create-next-app` + TypeScript + Tailwind 설정
   - Prisma 설치 및 스키마 정의, SQLite 마이그레이션

2. **Phase 2 — 백엔드 API**
   - Todo CRUD API 구현 (Route Handlers)
   - Category, Tag API 구현

3. **Phase 3 — 기본 UI (목록 뷰)**
   - 할 일 목록 컴포넌트
   - 추가/수정 모달
   - 완료 토글, 삭제

4. **Phase 4 — 필터 & 카테고리/태그**
   - 사이드바 필터 UI
   - 카테고리/태그 관리 UI

5. **Phase 5 — 주간 캘린더 뷰**
   - 주간 그리드 컴포넌트
   - 날짜별 할 일 배치
   - 주간 이동 네비게이션

---

## 8. 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| SQLite 파일 경로 (배포 환경) | 개발 환경 기준 로컬 파일, 배포 시 경로 설정 문서화 |
| 주간 뷰 타임존 이슈 | `date-fns` 라이브러리로 일관된 날짜 처리 |
| Prisma migration 충돌 | 스키마 변경 시 `prisma migrate dev` 사용 |

---

## 9. 성공 기준 (Success Criteria)

- [ ] SC-01: 할 일 CRUD 모든 항목 정상 동작
- [ ] SC-02: 완료 토글 후 상태 유지 (새로고침 후 확인)
- [ ] SC-03: 카테고리/태그 필터 정확히 작동
- [ ] SC-04: 주간 캘린더에서 마감일 기준으로 할 일이 올바른 날짜에 표시
- [ ] SC-05: 주간 이동 (이전/다음 주) 정상 작동
- [ ] SC-06: 모바일에서 레이아웃 깨지지 않음
