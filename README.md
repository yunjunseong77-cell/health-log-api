# 마이 헬스 로그 API

건강 기록을 저장하고 BMI, 혈압, 혈당을 학습용 기준으로 분석하는 FastAPI 미니 프로젝트입니다.

> 이 프로젝트의 분류 기준은 학습용으로 단순화되어 있으며 의학적 진단에 사용하지 않습니다.

## 기술 스택

- Python
- FastAPI
- Pydantic
- SQLite
- Docker

## 주요 기능

- 건강 기록 추가·전체 조회·단건 조회·수정·삭제
- BMI, 혈압, 혈당 분류 및 경고 자동 계산
- 날짜 범위 검색
- 전체 기록 통계 조회
- SQLite 저장으로 서버를 재시작해도 데이터 유지
- 과제 확인용 `data.json` 스냅샷 저장
- Swagger API 문서와 건강 기록 대시보드

## 로컬 실행

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

- API 문서: http://127.0.0.1:8000/docs
- 대시보드: http://127.0.0.1:8000/dashboard

## Docker 실행

```powershell
docker build -t health-log-api .
docker run --rm -p 8000:8000 -v "${PWD}:/app" health-log-api
```

컨테이너를 종료해도 SQLite 파일을 유지하려면 현재 폴더를 `/app`에 연결합니다.

## API 목록

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | API 상태 확인 |
| POST | `/records` | 건강 기록 추가 |
| GET | `/records` | 전체 기록 조회 |
| GET | `/records/{record_id}` | 특정 기록 조회 |
| PUT | `/records/{record_id}` | 기록 전체 수정 |
| DELETE | `/records/{record_id}` | 특정 기록 삭제 |
| GET | `/search?start=YYYY-MM-DD&end=YYYY-MM-DD` | 날짜 범위 검색 |
| GET | `/stats` | 기록 통계 조회 |
