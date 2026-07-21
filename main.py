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

def calculate_bmi(weight: float, height: float):
    height_m = height / 100
    bmi = weight / (height_m * height_m)

    return round(bmi, 2)

def classify_bmi(bmi: float):
    if bmi < 18.5:
        return "저체중"
    elif bmi < 23:
        return "정상"
    elif bmi < 25:
        return "과체중"
    else:
        return "비만"


# 기본 주소 확인용
@app.get("/")
def read_root():
    return {
        "message": "마이 헬스 로그 API"
    }


# 건강 기록 추가
@app.post("/records")
def create_record(record: RecordIn):
    bmi = calculate_bmi(record.weight, record.height)
    bmi_category = classify_bmi(bmi)

    bp_category = classify_blood_pressure(
        record.systolic,
        record.diastolic
    )

    sugar_category = classify_blood_sugar(
        record.blood_sugar
    )

    warnings = make_warnings(
        bmi_category,
        bp_category,
        sugar_category
    )

    new_record = {
        "id": len(records) + 1,
        **record.model_dump(),
        "bmi": bmi,
        "bmi_category": bmi_category,
        "bp_category": bp_category,
        "sugar_category": sugar_category,
        "warnings": warnings
    }

    records.append(new_record)

    return new_record

def classify_blood_pressure(systolic: int, diastolic: int):
    if systolic < 120 and diastolic < 80:
        return "정상"
    elif systolic < 140 and diastolic < 90:
        return "주의"
    else:
        return "고혈압"

def classify_blood_sugar(blood_sugar: int):
    if blood_sugar < 100:
        return "정상"
    elif blood_sugar < 126:
        return "공복혈당장애"
    else:
        return "당뇨 의심"

def make_warnings(
    bmi_category: str,
    bp_category: str,
    sugar_category: str
):
    warnings = []

    if bmi_category == "비만":
        warnings.append("BMI가 비만 범위입니다.")

    if bp_category == "고혈압":
        warnings.append("혈압이 고혈압 범위입니다.")

    if sugar_category == "당뇨 의심":
        warnings.append("혈당이 당뇨 의심 범위입니다.")

    return warnings

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

@app.put("/records/{record_id}")
def update_record(record_id: int, record: RecordIn):
    for index, old_record in enumerate(records):
        if old_record["id"] == record_id:
            # 수정된 값으로 다시 계산
            bmi = calculate_bmi(record.weight, record.height)
            bmi_category = classify_bmi(bmi)

            bp_category = classify_blood_pressure(
                record.systolic,
                record.diastolic
            )

            sugar_category = classify_blood_sugar(
                record.blood_sugar
            )

            warnings = make_warnings(
                bmi_category,
                bp_category,
                sugar_category
            )

            updated_record = {
                "id": record_id,
                **record.model_dump(),
                "bmi": bmi,
                "bmi_category": bmi_category,
                "bp_category": bp_category,
                "sugar_category": sugar_category,
                "warnings": warnings
            }

            records[index] = updated_record

            return updated_record

    raise HTTPException(
        status_code=404,
        detail="해당 기록을 찾을 수 없습니다."
    )