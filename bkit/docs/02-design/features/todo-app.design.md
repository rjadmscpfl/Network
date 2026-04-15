# [Design] todo-app

> **Feature**: Todo App (풀스택)
> **Created**: 2026-04-10
> **Phase**: Design
> **Architecture**: Option C — Pragmatic Balance
> **Status**: Draft

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 개인 할 일 관리의 불편함 해소 — 주간 단위 진행 관리 필요 |
| **WHO** | 개인 사용자 (인증 없음, 단일 사용자 기준) |
| **RISK** | SQLite 동시성 제한 (단일 사용자이므로 허용), 주간 뷰 날짜 계산 복잡도 |
| **SUCCESS** | 할 일 CRUD 정상 동작, 카테고리/태그 필터, 주간 캘린더 뷰에서 마감일별 표시 |
| **SCOPE** | Next.js App Router + Prisma ORM + SQLite, 인증 없음, Tailwind CSS |

---

## 1. 개요

**선택 아키텍처**: Option C — Pragmatic Balance

feature 단위 폴더 구조로 적절한 관심사 분리를 유지하면서 빠른 개발이 가능한 구조. Server Components를 최대한 활용하고 Client Component는 인터랙션이 필요한 곳에만 한정.

---

## 2. 프로젝트 구조

```
todo-app/
├── prisma/
│   ├── schema.prisma
│   └── dev.db                    # SQLite 파일
├── src/
│   ├── app/
│   │   ├── layout.tsx            # 루트 레이아웃
│   │   ├── page.tsx              # 목록 뷰 (Server Component)
│   │   ├── week/
│   │   │   └── page.tsx          # 주간 캘린더 뷰 (Server Component)
│   │   └── api/
│   │       ├── todos/
│   │       │   ├── route.ts      # GET(목록), POST(생성)
│   │       │   └── [id]/
│   │       │       ├── route.ts  # GET, PATCH, DELETE
│   │       │       └── toggle/
│   │       │           └── route.ts  # PATCH (완료 토글)
│   │       ├── todos/week/
│   │       │   └── route.ts      # GET (주간 데이터)
│   │       ├── categories/
│   │       │   ├── route.ts      # GET, POST
│   │       │   └── [id]/
│   │       │       └── route.ts  # DELETE
│   │       └── tags/
│   │           └── route.ts      # GET (자동완성용)
│   ├── components/
│   │   ├── TodoList.tsx          # 할 일 목록 (Client)
│   │   ├── TodoItem.tsx          # 할 일 단건 카드 (Client)
│   │   ├── TodoModal.tsx         # 추가/수정 모달 (Client)
│   │   ├── WeekCalendar.tsx      # 주간 그리드 (Client)
│   │   ├── WeekDayColumn.tsx     # 날짜별 컬럼 (Client)
│   │   ├── CategorySidebar.tsx   # 카테고리/태그 필터 사이드바 (Client)
│   │   └── TagInput.tsx          # 태그 자동완성 입력 (Client)
│   ├── lib/
│   │   ├── prisma.ts             # Prisma 클라이언트 싱글턴
│   │   └── date.ts               # date-fns 유틸 (주간 계산)
│   └── types/
│       └── index.ts              # 공통 타입 정의
├── package.json
├── tailwind.config.ts
└── tsconfig.json
```

---

## 3. 데이터 모델 (Prisma Schema)

```prisma
// prisma/schema.prisma

generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = env("DATABASE_URL")
}

model Todo {
  id          Int       @id @default(autoincrement())
  title       String
  description String?
  dueDate     DateTime?
  completed   Boolean   @default(false)
  createdAt   DateTime  @default(now())
  updatedAt   DateTime  @updatedAt
  categoryId  Int?
  category    Category? @relation(fields: [categoryId], references: [id])
  todoTags    TodoTag[]
}

model Category {
  id    Int    @id @default(autoincrement())
  name  String @unique
  color String @default("#6366f1")
  todos Todo[]
}

model Tag {
  id       Int       @id @default(autoincrement())
  name     String    @unique
  todoTags TodoTag[]
}

model TodoTag {
  todoId Int
  tagId  Int
  todo   Todo @relation(fields: [todoId], references: [id], onDelete: Cascade)
  tag    Tag  @relation(fields: [tagId], references: [id], onDelete: Cascade)

  @@id([todoId, tagId])
}
```

**환경 변수** (`.env`):
```
DATABASE_URL="file:./dev.db"
```

---

## 4. API 설계

### 4.1 Todos API

#### `GET /api/todos`
```
Query params:
  categoryId?: number
  tagId?: number
  completed?: boolean
  search?: string

Response: { todos: Todo[] }
```

#### `POST /api/todos`
```
Body: {
  title: string        // required
  description?: string
  dueDate?: string     // ISO 8601
  categoryId?: number
  tagNames?: string[]  // 태그 이름 배열 (upsert)
}
Response: { todo: Todo }
```

#### `PATCH /api/todos/[id]`
```
Body: Partial<{
  title, description, dueDate, categoryId, tagNames, completed
}>
Response: { todo: Todo }
```

#### `DELETE /api/todos/[id]`
```
Response: { success: true }
```

#### `PATCH /api/todos/[id]/toggle`
```
Response: { todo: Todo }  // completed 반전
```

#### `GET /api/todos/week`
```
Query params:
  weekStart: string  // ISO 8601 (해당 주 월요일)

Response: {
  todos: Todo[]  // dueDate가 해당 주 범위 내인 항목
}
```

### 4.2 Categories API

#### `GET /api/categories`
```
Response: { categories: Category[] }
```

#### `POST /api/categories`
```
Body: { name: string, color?: string }
Response: { category: Category }
```

#### `DELETE /api/categories/[id]`
```
Response: { success: true }
// 연결된 Todo의 categoryId는 null로 설정
```

### 4.3 Tags API

#### `GET /api/tags`
```
Query params:
  q?: string  // 검색어 (자동완성용)

Response: { tags: Tag[] }
```

---

## 5. 컴포넌트 설계

### 5.1 TodoList (Client Component)
```
Props: { initialTodos: Todo[], categories: Category[] }
State:
  - todos: Todo[]
  - filter: { categoryId?, tagId?, completed?, search? }
  - isModalOpen: boolean
  - editingTodo: Todo | null

기능:
  - 필터 변경 시 /api/todos 재호출
  - 완료 토글: PATCH /api/todos/[id]/toggle
  - 삭제: DELETE /api/todos/[id]
  - 추가/수정: TodoModal 열기
```

### 5.2 TodoModal (Client Component)
```
Props: {
  todo?: Todo        // undefined이면 추가 모드
  categories: Category[]
  onClose: () => void
  onSave: (todo: Todo) => void
}
State:
  - formData: { title, description, dueDate, categoryId, tagNames[] }
  - tagInput: string  // 현재 입력 중인 태그
  - tagSuggestions: Tag[]  // 자동완성 목록

기능:
  - 저장: POST 또는 PATCH /api/todos
  - 태그 입력 시 GET /api/tags?q=... 호출 (debounce 300ms)
```

### 5.3 WeekCalendar (Client Component)
```
Props: { initialTodos: Todo[], initialWeekStart: string }
State:
  - weekStart: Date  // 현재 주의 월요일
  - todos: Todo[]

기능:
  - 주간 이동: weekStart ±7일, /api/todos/week 재호출
  - 7개 WeekDayColumn 렌더링
  - 각 날짜에 해당하는 할 일 카드 표시
```

### 5.4 WeekDayColumn (Client Component)
```
Props: { date: Date, todos: Todo[] }
기능:
  - 날짜 헤더 (요일, 날짜)
  - 해당 날짜 할 일 카드 목록
  - 완료 토글 가능
```

### 5.5 CategorySidebar (Client Component)
```
Props: { categories: Category[], activeFilter: Filter }
기능:
  - 전체 / 카테고리별 필터 선택
  - 카테고리 추가/삭제
```

---

## 6. 페이지 설계

### 6.1 목록 뷰 (`/`)

```
레이아웃:
┌─────────────────────────────────────────┐
│  [Todo App]          [주간 뷰 링크]      │  ← 헤더
├──────────┬──────────────────────────────┤
│ 전체     │  [+ 할 일 추가]  [검색창]    │
│ 카테고리1│                               │
│ 카테고리2│  □ 할 일 제목        [삭제]  │
│ ──────── │    마감: 4/11 | 태그: work   │
│ #태그1   │  ─────────────────────────── │
│ #태그2   │  ✓ 완료된 할 일      [삭제]  │
└──────────┴──────────────────────────────┘
```

### 6.2 주간 캘린더 뷰 (`/week`)

```
레이아웃:
┌──────────────────────────────────────────────────────┐
│  [← 이전 주]  2026년 4월 6일 ~ 4월 12일  [다음 주 →] │
├──────┬──────┬──────┬──────┬──────┬──────┬──────┬─────┤
│ 월6  │ 화7  │ 수8  │ 목9  │ 금10 │ 토11 │ 일12 │
├──────┼──────┼──────┼──────┼──────┼──────┼──────┤
│ 할일 │      │ 할일 │      │ 할일 │      │      │
│ 카드 │      │ 카드 │      │ 카드 │      │      │
└──────┴──────┴──────┴──────┴──────┴──────┴──────┘
```

---

## 7. 상태 관리 전략

- **서버 상태**: Next.js Server Components + `revalidatePath` (App Router)
- **클라이언트 상태**: `useState` / `useReducer` (컴포넌트 로컬)
- **외부 라이브러리 불필요**: Zustand/Redux 없이 Props drilling 최소화
- **데이터 흐름**: Server Component에서 초기 데이터 fetch → Client Component에 props 전달 → 변경 시 fetch API 호출 후 local state 업데이트

---

## 8. 테스트 계획

| 레벨 | 항목 | 방법 |
|------|------|------|
| L1 API | POST /api/todos 생성 확인 | curl |
| L1 API | PATCH toggle 상태 반전 확인 | curl |
| L1 API | GET /api/todos/week 범위 필터 | curl |
| L2 UI | TodoModal 저장 → 목록에 추가 | 수동 |
| L2 UI | 완료 체크 → 체크 상태 유지 (새로고침) | 수동 |
| L2 UI | 카테고리 필터 → 해당 항목만 표시 | 수동 |
| L3 E2E | 할 일 추가 → 주간 뷰에서 확인 | 수동 |

---

## 9. 의존성

```json
{
  "dependencies": {
    "next": "^14",
    "@prisma/client": "^5",
    "date-fns": "^3",
    "clsx": "^2"
  },
  "devDependencies": {
    "prisma": "^5",
    "typescript": "^5",
    "@types/node": "^20",
    "@types/react": "^18",
    "tailwindcss": "^3",
    "autoprefixer": "^10"
  }
}
```

---

## 10. 환경 설정

```bash
# 프로젝트 초기화
npx create-next-app@latest todo-app --typescript --tailwind --app --src-dir

# Prisma 설치
npm install prisma @prisma/client
npx prisma init --datasource-provider sqlite

# date-fns 설치
npm install date-fns clsx

# DB 마이그레이션
npx prisma migrate dev --name init

# 개발 서버
npm run dev
```

---

## 11. 구현 가이드

### 11.1 구현 순서

1. 프로젝트 초기화 + Prisma 스키마 마이그레이션
2. `lib/prisma.ts` 싱글턴 설정
3. API Route Handlers 구현 (todos CRUD → toggle → week → categories → tags)
4. `types/index.ts` 공통 타입
5. 목록 뷰 UI (TodoList → TodoItem → TodoModal → TagInput)
6. CategorySidebar + 필터 연동
7. 주간 뷰 (WeekCalendar → WeekDayColumn)
8. 반응형 스타일 조정

### 11.2 핵심 구현 포인트

**Prisma 싱글턴** (`lib/prisma.ts`):
```typescript
import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as { prisma: PrismaClient }

export const prisma =
  globalForPrisma.prisma ?? new PrismaClient()

if (process.env.NODE_ENV !== 'production')
  globalForPrisma.prisma = prisma
```

**주간 날짜 범위** (`lib/date.ts`):
```typescript
import { startOfWeek, endOfWeek, addDays, format } from 'date-fns'
import { ko } from 'date-fns/locale'

export function getWeekRange(date: Date) {
  const start = startOfWeek(date, { weekStartsOn: 1 }) // 월요일 시작
  const end = endOfWeek(date, { weekStartsOn: 1 })
  return { start, end }
}

export function getWeekDays(weekStart: Date): Date[] {
  return Array.from({ length: 7 }, (_, i) => addDays(weekStart, i))
}
```

**태그 upsert** (API Route에서):
```typescript
// tagNames 배열을 받아 upsert 후 TodoTag 연결
const tagConnections = await Promise.all(
  tagNames.map(name =>
    prisma.tag.upsert({
      where: { name },
      update: {},
      create: { name },
    })
  )
)
```

### 11.3 Session Guide

| Module | 내용 | 예상 파일 수 |
|--------|------|-------------|
| module-1 | 프로젝트 초기화 + Prisma 스키마 + lib/ | 4개 |
| module-2 | API Route Handlers (todos, categories, tags) | 7개 |
| module-3 | 목록 뷰 컴포넌트 (TodoList, TodoItem, TodoModal, TagInput) | 5개 |
| module-4 | CategorySidebar + 필터 연동 + 목록 페이지 | 3개 |
| module-5 | 주간 캘린더 뷰 (WeekCalendar, WeekDayColumn, week/page) | 3개 |

```bash
# 모듈별 구현
/pdca do todo-app --scope module-1    # 초기화 + DB
/pdca do todo-app --scope module-2    # API
/pdca do todo-app --scope module-3    # 목록 UI
/pdca do todo-app --scope module-4    # 필터
/pdca do todo-app --scope module-5    # 주간 뷰
```
