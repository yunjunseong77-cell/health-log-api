from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


# FastAPI 앱 생성
app = FastAPI(title="마이 헬스 로그 API")


# 임시 저장 공간
# 아직은 메모리에만 저장합니다.
records = []


# 기록을 입력받을 때 사용할 데이터 형식
class RecordIn(BaseModel):
    date: str
    weight: float
    height: float
    systolic: int
    diastolic: int
    blood_sugar: int
    steps: int = 0
    sleep_hours: float = 0.0
    memo: str = ""


# 기본 주소 확인용
@app.get("/")
def read_root():
    return {
        "message": "마이 헬스 로그 API"
    }


# 건강 기록 추가
@app.post("/records")
def create_record(record: RecordIn):
    new_record = {
        "id": len(records) + 1,
        **record.model_dump()
    }

    records.append(new_record)

    return new_record


# 전체 건강 기록 조회
@app.get("/records")
def get_records():
    return {
        "count": len(records),
        "records": records
    }

@app.get("/records/{record_id}")
def get_record(record_id: int):
    for record in records:
        if record["id"] == record_id:
            return record

    raise HTTPException(
        status_code=404,
        detail="해당 기록을 찾을 수 없습니다."
    )

@app.delete("/records/{record_id}")
def delete_record(record_id: int):
    for index, record in enumerate(records):
        if record["id"] == record_id:
            deleted_record = records.pop(index)

            return {
                "message": "기록이 삭제되었습니다.",
                "record": deleted_record
            }

    raise HTTPException(
        status_code=404,
        detail="해당 기록을 찾을 수 없습니다."
    )