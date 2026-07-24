from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator
import sqlite3
import json
import base64
import hashlib
import hmac
import secrets
import time
import os
from pathlib import Path
from datetime import date as date_type, datetime
from zoneinfo import ZoneInfo


# -----------------------------
# FastAPI 앱 설정
# -----------------------------

app = FastAPI(
    docs_url=None,
    title="마이 헬스 로그 API",
    version="1.0.0",
    description="""
건강 기록을 저장하고 BMI, 혈압, 혈당을 분석하는 API입니다.

※ 본 API의 건강 분류 기준은 학습용으로 단순화한 기준입니다.
실제 의학적 진단에 사용하지 마세요.
""",
    openapi_tags=[
        {
            "name": "건강 기록",
            "description": "건강 기록을 추가, 조회, 수정, 삭제하는 기능"
        },
        {
            "name": "검색",
            "description": "날짜 범위로 건강 기록을 검색하는 기능"
        },
        {
            "name": "통계",
            "description": "저장된 건강 기록의 기본 통계를 확인하는 기능"
        }
    ]
)


# -----------------------------
# SQLite 설정
# -----------------------------

DB_FILE = Path(__file__).resolve().parent / "health_records.db"
JSON_FILE = Path(__file__).resolve().parent / "data.json"
KST = ZoneInfo("Asia/Seoul")


def get_connection():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            weight REAL NOT NULL,
            height REAL NOT NULL,
            systolic INTEGER NOT NULL,
            diastolic INTEGER NOT NULL,
            blood_sugar INTEGER NOT NULL,
            steps INTEGER DEFAULT 0,
            sleep_hours REAL DEFAULT 0.0,
            memo TEXT DEFAULT '',
            bmi REAL NOT NULL,
            bmi_category TEXT NOT NULL,
            bp_category TEXT NOT NULL,
            sugar_category TEXT NOT NULL,
            warnings TEXT NOT NULL DEFAULT '[]'
        )
    """)

    # 기존 DB에도 사용자 연결 컬럼을 안전하게 추가합니다.
    record_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(records)").fetchall()
    }
    if "user_id" not in record_columns:
        connection.execute("ALTER TABLE records ADD COLUMN user_id INTEGER")

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_records_user_id ON records(user_id)"
    )

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            occurred_at TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT '기타',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id, occurred_at)"
    )

    connection.commit()
    connection.close()


init_db()


# -----------------------------
# 로그인·회원가입
# -----------------------------

AUTH_SECRET = os.getenv("AUTH_SECRET", "health-log-api-change-this-secret")


class SignupIn(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    login: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=128)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return base64.urlsafe_b64encode(salt + digest).decode()


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(stored_hash.encode())
        salt, expected = decoded[:16], decoded[16:]
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, base64.binascii.Error):
        return False


def create_session_token(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}"
    signature = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def get_session_user(request: Request):
    token = request.cookies.get("health_session")
    if not token:
        return None
    try:
        user_id, issued_at, signature = token.split(":", 2)
        payload = f"{user_id}:{issued_at}"
        expected = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if int(time.time()) - int(issued_at) > 60 * 60 * 24 * 7:
            return None
        connection = get_connection()
        user = connection.execute("SELECT id, username, email FROM users WHERE id = ?", (int(user_id),)).fetchone()
        connection.close()
        return user
    except (ValueError, TypeError):
        return None


def require_session_user(request: Request):
    user = get_session_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="로그인이 필요한 기능입니다."
        )
    return user


def auth_page(mode: str) -> str:
    is_login = mode == "login"
    title = "다시 만나서 반가워요" if is_login else "건강한 기록을 시작해요"
    subtitle = "오늘의 몸 상태를 가볍게 기록해보세요." if is_login else "나만의 건강 기록 공간을 만들어보세요."
    action = "로그인" if is_login else "회원가입"
    switch_text = "아직 계정이 없나요?" if is_login else "이미 계정이 있나요?"
    switch_href = "/signup" if is_login else "/login"
    switch_action = "회원가입" if is_login else "로그인"
    login_field = "" if is_login else '<label>닉네임<input id="username" placeholder="2자 이상 입력" /></label>'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{action} · 마이 헬스 로그</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;background:linear-gradient(135deg,#effcf7,#fff8ef);font-family:Arial,'Malgun Gothic',sans-serif;color:#233b35;display:flex;align-items:center;justify-content:center;padding:24px}}
.shell{{width:min(980px,100%);display:grid;grid-template-columns:1fr 1fr;background:#fff;border-radius:28px;overflow:hidden;box-shadow:0 24px 70px #40776b22}}
.hero{{padding:56px 48px;background:linear-gradient(145deg,#30b982,#79d9ac);color:white;position:relative;overflow:hidden}}
.hero:after{{content:'♡';position:absolute;font-size:240px;right:-20px;bottom:-70px;color:#ffffff2b}}
.logo{{font-size:18px;font-weight:700;letter-spacing:.5px}} h1{{font-size:36px;line-height:1.3;margin:90px 0 18px;position:relative;z-index:1}} .hero p{{font-size:17px;line-height:1.7;position:relative;z-index:1}}
.form{{padding:56px 48px}} h2{{margin:0 0 10px;font-size:28px}} .subtitle{{color:#71827d;margin:0 0 28px}} label{{display:block;font-size:14px;font-weight:700;margin:18px 0 8px}} input{{width:100%;padding:14px 15px;border:1px solid #d7e5df;border-radius:12px;font-size:15px;outline:none}} input:focus{{border-color:#35bd83;box-shadow:0 0 0 4px #35bd8320}}
button{{width:100%;border:0;border-radius:12px;padding:15px;background:#29b77d;color:#fff;font-size:16px;font-weight:700;cursor:pointer;margin-top:26px}} button:hover{{background:#189d68}} .switch{{text-align:center;color:#758580;margin-top:22px;font-size:14px}} a{{color:#169b68;font-weight:700;text-decoration:none}} .message{{min-height:22px;color:#d05252;text-align:center;margin-top:14px;font-size:14px}}
@media(max-width:700px){{.shell{{grid-template-columns:1fr}}.hero{{padding:34px}}.hero h1{{margin-top:50px}}.form{{padding:34px}}}}
</style></head><body><main class="shell"><section class="hero"><div class="logo">🌿 마이 헬스 로그</div><h1>{title}</h1><p>{subtitle}<br>작은 기록이 나를 돌보는 습관이 돼요.</p></section><section class="form"><h2>{action}</h2><p class="subtitle">{subtitle}</p><form id="auth-form">{login_field}<label>{'이메일 또는 닉네임' if is_login else '이메일'}<input id="login" type="{'text' if is_login else 'email'}" placeholder="{'이메일 또는 닉네임 입력' if is_login else 'you@example.com'}" required /></label><label>비밀번호<input id="password" type="password" placeholder="8자 이상 입력" required /></label><button type="submit">{action}</button></form><div id="message" class="message"></div><p class="switch">{switch_text} <a href="{switch_href}">{switch_action}</a></p></section></main>
<script>
document.getElementById('auth-form').addEventListener('submit', async (event) => {{
  event.preventDefault();
  const message = document.getElementById('message');
  message.textContent = '처리 중이에요...';
  const body = {{login: document.getElementById('login').value, password: document.getElementById('password').value}};
  {'body.username = document.getElementById(\'username\').value; body.email = body.login;' if not is_login else ''}
  const response = await fetch('/auth/{'login' if is_login else 'signup'}', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({'body' if is_login else 'body'})}});
  const data = await response.json();
  if (!response.ok) {{ message.textContent = data.detail || '다시 시도해주세요.'; return; }}
  if ({'true' if is_login else 'false'}) {{ window.location.href = '/dashboard'; }} else {{ window.location.href = '/login?registered=1'; }}
}});
</script></body></html>"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    return HTMLResponse(auth_page("login"))


@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page():
    return HTMLResponse(auth_page("signup"))


@app.post("/auth/signup")
def signup(payload: SignupIn):
    email = payload.email.strip().lower()
    username = payload.username.strip()
    connection = get_connection()
    try:
        existing_user_count = connection.execute(
            "SELECT COUNT(*) AS count FROM users"
        ).fetchone()["count"]
        cursor = connection.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, hash_password(payload.password))
        )

        # 인증 기능을 추가하기 전부터 있던 기록은 첫 계정의 기록으로 연결합니다.
        if existing_user_count == 0:
            connection.execute(
                "UPDATE records SET user_id = ? WHERE user_id IS NULL",
                (cursor.lastrowid,)
            )

        connection.commit()
        return {"message": "회원가입이 완료되었습니다.", "user_id": cursor.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="이미 사용 중인 이메일 또는 닉네임입니다.")
    finally:
        connection.close()


@app.post("/auth/login")
def login(payload: LoginIn, response: Response):
    login_value = payload.login.strip().lower()
    connection = get_connection()
    user = connection.execute(
        "SELECT * FROM users WHERE lower(email) = ? OR lower(username) = ?",
        (login_value, login_value)
    ).fetchone()
    connection.close()
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="이메일/닉네임 또는 비밀번호를 확인해주세요.")
    response.set_cookie("health_session", create_session_token(user["id"]), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return {"message": "로그인되었습니다.", "username": user["username"]}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("health_session")
    return {"message": "로그아웃되었습니다."}


# -----------------------------
# 입력 데이터 형식
# -----------------------------

class RecordIn(BaseModel):
    date: str
    weight: float = Field(gt=0, description="몸무게(kg), 0보다 커야 합니다.")
    height: float = Field(gt=0, description="키(cm), 0보다 커야 합니다.")
    systolic: int = Field(gt=0, description="수축기 혈압, 0보다 커야 합니다.")
    diastolic: int = Field(gt=0, description="이완기 혈압, 0보다 커야 합니다.")
    blood_sugar: int = Field(ge=0, description="공복 혈당, 0 이상이어야 합니다.")
    steps: int = Field(default=0, ge=0, description="걸음 수, 0 이상이어야 합니다.")
    sleep_hours: float = Field(default=0.0, ge=0, description="수면 시간, 0 이상이어야 합니다.")
    memo: str = ""

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str):
        try:
            date_type.fromisoformat(value)
        except ValueError as error:
            raise ValueError("date는 YYYY-MM-DD 형식이어야 합니다.") from error
        return value


class EventIn(BaseModel):
    raw_text: str = Field(min_length=1, max_length=500, description="생활 이벤트 원문")
    event_type: str = Field(default="기타", max_length=30, description="이벤트 종류")
    occurred_at: str = Field(default="", description="발생 시각")


# -----------------------------
# 건강 분석 함수
# -----------------------------

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


def analyze_record(record: RecordIn):
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

    return {
        "bmi": bmi,
        "bmi_category": bmi_category,
        "bp_category": bp_category,
        "sugar_category": sugar_category,
        "warnings": warnings
    }


# SQLite 한 행을 API 응답용 딕셔너리로 변환
def row_to_record(row):
    record = dict(row)

    # SQLite에는 문자열로 저장되어 있으므로 리스트로 변환
    record["warnings"] = json.loads(record["warnings"])

    return record


def row_to_event(row):
    return dict(row)


def save_json_snapshot(connection=None):
    """현재 SQLite 기록을 과제 제출용 JSON 파일로도 저장합니다."""
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()

    rows = connection.execute(
        "SELECT * FROM records ORDER BY id"
    ).fetchall()

    payload = {
        "count": len(rows),
        "records": [row_to_record(row) for row in rows]
    }

    JSON_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    if owns_connection:
        connection.close()


# -----------------------------
# 기본 주소
# -----------------------------

@app.get(
    "/",
    summary="API 상태 확인",
    description="마이 헬스 로그 API가 정상적으로 실행 중인지 확인합니다.",
    tags=["건강 기록"]
)
def read_root():
    return {
        "message": "마이 헬스 로그 API가 정상적으로 실행 중입니다."
    }


# -----------------------------
# 건강 기록 추가
# -----------------------------

@app.post(
    "/records",
    summary="건강 기록 추가",
    description="건강 기록을 SQLite 데이터베이스에 저장하고 건강 상태를 자동으로 분석합니다.",
    tags=["건강 기록"]
)
def create_record(record: RecordIn, request: Request):
    analysis = analyze_record(record)
    user = require_session_user(request)

    connection = get_connection()

    cursor = connection.execute(
        """
        INSERT INTO records (
            date,
            weight,
            height,
            systolic,
            diastolic,
            blood_sugar,
            steps,
            sleep_hours,
            memo,
            bmi,
            bmi_category,
            bp_category,
            sugar_category,
            warnings,
            user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.date,
            record.weight,
            record.height,
            record.systolic,
            record.diastolic,
            record.blood_sugar,
            record.steps,
            record.sleep_hours,
            record.memo,
            analysis["bmi"],
            analysis["bmi_category"],
            analysis["bp_category"],
            analysis["sugar_category"],
            json.dumps(
                analysis["warnings"],
                ensure_ascii=False
            ),
            user["id"]
        )
    )

    connection.commit()

    new_id = cursor.lastrowid

    row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND user_id = ?",
        (new_id, user["id"])
    ).fetchone()

    save_json_snapshot(connection)

    connection.close()

    return row_to_record(row)


# -----------------------------
# 전체 기록 조회
# -----------------------------

@app.get(
    "/records",
    summary="전체 건강 기록 조회",
    description="저장된 모든 건강 기록과 전체 개수를 반환합니다.",
    tags=["건강 기록"]
)
def get_records(request: Request):
    connection = get_connection()
    user = require_session_user(request)

    rows = connection.execute(
        "SELECT * FROM records WHERE user_id = ? ORDER BY id",
        (user["id"],)
    ).fetchall()

    connection.close()

    result = [row_to_record(row) for row in rows]

    return {
        "count": len(result),
        "records": result
    }


# -----------------------------
# 특정 기록 조회
# -----------------------------

@app.get(
    "/records/{record_id}",
    summary="특정 건강 기록 조회",
    description="ID를 이용해 건강 기록 하나를 조회합니다.",
    tags=["건강 기록"]
)
def get_record(record_id: int, request: Request):
    connection = get_connection()
    user = require_session_user(request)

    row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND user_id = ?",
        (record_id, user["id"])
    ).fetchone()

    connection.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="해당 건강 기록을 찾을 수 없습니다."
        )

    return row_to_record(row)


# -----------------------------
# 건강 기록 수정
# -----------------------------

@app.put(
    "/records/{record_id}",
    summary="건강 기록 수정",
    description="기존 건강 기록을 수정하고 BMI와 건강 분류를 다시 계산합니다.",
    tags=["건강 기록"]
)
def update_record(record_id: int, record: RecordIn, request: Request):
    analysis = analyze_record(record)
    user = require_session_user(request)

    connection = get_connection()

    existing_row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND user_id = ?",
        (record_id, user["id"])
    ).fetchone()

    if existing_row is None:
        connection.close()

        raise HTTPException(
            status_code=404,
            detail="수정할 건강 기록을 찾을 수 없습니다."
        )

    connection.execute(
        """
        UPDATE records
        SET
            date = ?,
            weight = ?,
            height = ?,
            systolic = ?,
            diastolic = ?,
            blood_sugar = ?,
            steps = ?,
            sleep_hours = ?,
            memo = ?,
            bmi = ?,
            bmi_category = ?,
            bp_category = ?,
            sugar_category = ?,
            warnings = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            record.date,
            record.weight,
            record.height,
            record.systolic,
            record.diastolic,
            record.blood_sugar,
            record.steps,
            record.sleep_hours,
            record.memo,
            analysis["bmi"],
            analysis["bmi_category"],
            analysis["bp_category"],
            analysis["sugar_category"],
            json.dumps(
                analysis["warnings"],
                ensure_ascii=False
            ),
            record_id,
            user["id"]
        )
    )

    connection.commit()

    updated_row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND user_id = ?",
        (record_id, user["id"])
    ).fetchone()

    save_json_snapshot(connection)

    connection.close()

    return row_to_record(updated_row)


# -----------------------------
# 건강 기록 삭제
# -----------------------------

@app.delete(
    "/records/{record_id}",
    summary="건강 기록 삭제",
    description="ID를 이용해 건강 기록 하나를 삭제합니다.",
    tags=["건강 기록"]
)
def delete_record(record_id: int, request: Request):
    connection = get_connection()
    user = require_session_user(request)

    row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND user_id = ?",
        (record_id, user["id"])
    ).fetchone()

    if row is None:
        connection.close()

        raise HTTPException(
            status_code=404,
            detail="삭제할 건강 기록을 찾을 수 없습니다."
        )

    connection.execute(
        "DELETE FROM records WHERE id = ? AND user_id = ?",
        (record_id, user["id"])
    )

    connection.commit()
    save_json_snapshot(connection)
    connection.close()

    return {
        "message": "건강 기록이 삭제되었습니다.",
        "record": row_to_record(row)
    }


# -----------------------------
# 날짜 범위 검색
# -----------------------------

@app.get(
    "/search",
    summary="날짜 범위로 건강 기록 검색",
    description="시작 날짜와 종료 날짜 사이의 건강 기록을 검색합니다.",
    tags=["검색"]
)
def search_records(start: str, end: str, request: Request):
    try:
        start_date = date_type.fromisoformat(start)
        end_date = date_type.fromisoformat(end)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="start와 end는 YYYY-MM-DD 형식이어야 합니다."
        ) from error

    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="start는 end보다 늦을 수 없습니다."
        )

    connection = get_connection()
    user = require_session_user(request)

    rows = connection.execute(
        """
        SELECT * FROM records
        WHERE user_id = ? AND date >= ? AND date <= ?
        ORDER BY date, id
        """,
        (user["id"], start, end)
    ).fetchall()

    connection.close()

    result = [row_to_record(row) for row in rows]

    return {
        "start": start,
        "end": end,
        "count": len(result),
        "records": result
    }


# -----------------------------
# 건강 기록 통계
# -----------------------------

@app.get(
    "/stats",
    summary="건강 기록 통계 조회",
    description="저장된 기록의 개수, 평균값, 분류별 개수를 반환합니다.",
    tags=["통계"]
)
def get_stats(request: Request):
    connection = get_connection()
    user = require_session_user(request)

    summary_row = connection.execute(
        """
        SELECT
            COUNT(*) AS count,
            COALESCE(AVG(weight), 0) AS average_weight,
            COALESCE(AVG(bmi), 0) AS average_bmi,
            COALESCE(AVG(systolic), 0) AS average_systolic,
            COALESCE(AVG(diastolic), 0) AS average_diastolic,
            COALESCE(AVG(blood_sugar), 0) AS average_blood_sugar,
            COALESCE(AVG(steps), 0) AS average_steps,
            COALESCE(AVG(sleep_hours), 0) AS average_sleep_hours,
            MIN(date) AS earliest_date,
            MAX(date) AS latest_date
        FROM records
        WHERE user_id = ?
        """
    , (user["id"],)).fetchone()

    def count_by(column):
        rows = connection.execute(
            f"SELECT {column} AS category, COUNT(*) AS count FROM records WHERE user_id = ? GROUP BY {column}",
            (user["id"],)
        ).fetchall()
        return {row["category"]: row["count"] for row in rows}

    result = {
        "count": summary_row["count"],
        "averages": {
            "weight": round(summary_row["average_weight"], 2),
            "bmi": round(summary_row["average_bmi"], 2),
            "systolic": round(summary_row["average_systolic"], 2),
            "diastolic": round(summary_row["average_diastolic"], 2),
            "blood_sugar": round(summary_row["average_blood_sugar"], 2),
            "steps": round(summary_row["average_steps"], 2),
            "sleep_hours": round(summary_row["average_sleep_hours"], 2)
        },
        "date_range": {
            "earliest": summary_row["earliest_date"],
            "latest": summary_row["latest_date"]
        },
        "category_counts": {
            "bmi": count_by("bmi_category"),
            "blood_pressure": count_by("bp_category"),
            "blood_sugar": count_by("sugar_category")
        }
    }

    connection.close()
    return result


# -----------------------------
# 귀여운 건강 기록 대시보드
# -----------------------------

@app.get("/docs", include_in_schema=False)
def custom_docs():
    swagger_page = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="마이 헬스 로그 API - 문서",
        swagger_ui_parameters={"docExpansion": "list"},
    )

    helper = r'''
<style>
#put-helper{display:none;position:fixed;top:85px;right:24px;z-index:99999;width:320px;padding:16px;border:2px solid #ff9f43;border-radius:14px;background:#fff8ef;box-shadow:0 8px 24px #0003;font-family:Arial,sans-serif}
#put-helper.visible{display:block}
#put-helper strong{color:#7b4b16;font-size:16px}#put-helper p{font-size:13px;line-height:1.5;color:#5c4630}#put-helper div{display:flex;gap:8px}#put-helper input{width:150px;padding:8px;border:1px solid #d8c2a5;border-radius:8px}#put-helper button{padding:8px 12px;border:0;border-radius:8px;background:#ff9f43;color:white;font-weight:bold;cursor:pointer}#put-helper span{display:block;margin-top:8px;font-size:13px;color:#7b4b16}
#post-helper{display:none;position:fixed;left:24px;bottom:24px;z-index:99999;width:330px;padding:18px;border:2px solid #49c98b;border-radius:14px;background:#effff6;box-shadow:0 8px 24px #0003;font-family:Arial,sans-serif}
#post-helper.visible{display:block}
#post-helper strong{color:#18794e;font-size:16px}#post-helper p{margin:8px 0 12px;font-size:13px;line-height:1.5;color:#35614d}#post-helper label{display:block;margin:8px 0 4px;color:#35614d;font-size:12px;font-weight:bold}#post-helper input,#post-helper textarea{width:100%;padding:7px;border:1px solid #b8dfca;border-radius:8px;background:#fff;font:inherit;box-sizing:border-box}#post-helper textarea{min-height:62px;resize:vertical}#post-helper button{width:100%;margin-top:12px;padding:9px;border:0;border-radius:8px;background:#31b978;color:white;font-weight:bold;cursor:pointer}#post-helper button:hover{background:#239b61}#post-message{display:block;margin-top:9px;font-size:12px;color:#18794e;line-height:1.4}
</style>
<div id="put-helper"><strong>✏️ PUT 수정 도우미</strong><p>ID를 입력하면 기존 기록을 PUT 입력창에 자동으로 불러옵니다.</p><div><input id="put-id" type="number" min="1" placeholder="기록 ID 예: 2"><button id="put-load">불러오기</button></div><span id="put-msg">기록 ID를 입력해주세요.</span></div>
<div id="post-helper"><strong>📝 건강 기록 빠른 입력</strong><p>건강 기록의 모든 항목을 입력하면 저장 후 BMI와 건강 분류를 자동으로 계산합니다.</p><form id="quick-record-form"><label for="quick-date">날짜</label><input id="quick-date" type="date" required><label for="quick-weight">몸무게 (kg)</label><input id="quick-weight" type="number" min="0.1" step="0.1" required><label for="quick-height">키 (cm)</label><input id="quick-height" type="number" min="0.1" step="0.1" required><label for="quick-systolic">수축기 혈압</label><input id="quick-systolic" type="number" min="1" required><label for="quick-diastolic">이완기 혈압</label><input id="quick-diastolic" type="number" min="1" required><label for="quick-sugar">공복 혈당</label><input id="quick-sugar" type="number" min="1" required><label for="quick-steps">걸음 수</label><input id="quick-steps" type="number" min="0" value="0" required><label for="quick-sleep">수면 시간</label><input id="quick-sleep" type="number" min="0" step="0.1" value="0" required><label for="quick-memo">메모</label><textarea id="quick-memo" placeholder="오늘의 컨디션이나 특이사항을 입력하세요."></textarea><button type="submit">기록 저장하기</button></form><span id="post-message">POST 탭을 펼치면 입력할 수 있어요.</span></div>
<script>
(() => {
  const helper=document.getElementById('put-helper');
  const postHelper=document.getElementById('post-helper');
  const syncVisibility=() => {
    const put=document.querySelector('.opblock-put');
    const post=document.querySelector('.opblock-post');
    helper.classList.toggle('visible', Boolean(put && put.classList.contains('is-open')));
    postHelper.classList.toggle('visible', Boolean(post && post.classList.contains('is-open')));
  };
  new MutationObserver(syncVisibility).observe(document.body,{subtree:true,attributes:true,attributeFilter:['class']});
  document.addEventListener('click',()=>setTimeout(syncVisibility,50));
  setInterval(syncVisibility,500);
  syncVisibility();
  const msg = (text, error=false) => { const e=document.getElementById('put-msg'); e.textContent=text; e.style.color=error?'#c0392b':'#7b4b16'; };
  const setValue = (e, value) => { e.value=value; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); };
  document.getElementById('put-load').onclick = async () => {
    const id=Number(document.getElementById('put-id').value);
    if(!Number.isInteger(id)||id<1){msg('1 이상의 ID를 입력해주세요.',true);return;}
    msg('불러오는 중입니다...');
    try{
      const res=await fetch('/records/'+id);
      if(!res.ok){msg('해당 기록을 찾을 수 없어요.',true);return;}
      const record=await res.json();
      const put=document.querySelector('.opblock-put');
      if(!put){msg('PUT 화면을 찾지 못했어요.',true);return;}
      if(!put.classList.contains('is-open')) put.querySelector('.opblock-summary')?.click();
      setTimeout(()=>{
        const path=put.querySelector('input[placeholder="record_id"]')||put.querySelector('input'); if(path)setValue(path,String(id));
        const tryButton=put.querySelector('button.try-out__btn'); if(tryButton&&/Try it out/i.test(tryButton.textContent))tryButton.click();
        setTimeout(()=>{
          const body=put.querySelector('textarea.body-param__text')||put.querySelector('textarea');
          if(!body){msg('PUT의 Try it out을 먼저 눌러주세요.',true);return;}
          setValue(body,JSON.stringify({date:record.date,weight:record.weight,height:record.height,systolic:record.systolic,diastolic:record.diastolic,blood_sugar:record.blood_sugar,steps:record.steps,sleep_hours:record.sleep_hours,memo:record.memo},null,2));
          msg('#'+id+' 기록을 불러왔어요. 원하는 값만 수정하세요.');
        },500);
      },500);
    }catch(e){msg('서버에 연결하지 못했어요.',true);}
  };

  const quickForm=document.getElementById('quick-record-form');
  const postMessage=document.getElementById('post-message');
  document.getElementById('quick-date').value=new Date().toISOString().slice(0,10);
  quickForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    postMessage.textContent='저장 중입니다...';
    try {
      const response=await fetch('/records', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          date:document.getElementById('quick-date').value,
          weight:Number(document.getElementById('quick-weight').value),
          height:Number(document.getElementById('quick-height').value),
          systolic:Number(document.getElementById('quick-systolic').value),
          diastolic:Number(document.getElementById('quick-diastolic').value),
          blood_sugar:Number(document.getElementById('quick-sugar').value),
          steps:Number(document.getElementById('quick-steps').value),
          sleep_hours:Number(document.getElementById('quick-sleep').value),
          memo:document.getElementById('quick-memo').value
        })
      });
      const data=await response.json();
      if(!response.ok){postMessage.textContent='저장 실패: '+(data.detail || '입력값을 확인해주세요.');postMessage.style.color='#c0392b';return;}
      postMessage.textContent=`#${data.id} 저장 완료! BMI ${data.bmi}, ${data.bmi_category}`;
      quickForm.reset();
      document.getElementById('quick-date').value=new Date().toISOString().slice(0,10);
      document.getElementById('quick-steps').value=0;
      document.getElementById('quick-sleep').value=0;
      postMessage.style.color='#18794e';
    } catch(error) {
      postMessage.textContent='서버에 연결하지 못했어요.';
      postMessage.style.color='#c0392b';
    }
  });
})();
</script>
'''
    page = swagger_page.body.decode("utf-8")
    return HTMLResponse(page.replace("</body>", helper + "</body>"))


@app.post(
    "/events",
    summary="생활 이벤트 빠른 기록",
    description="커피, 식사, 운동 등 생활 중 발생한 이벤트를 저장합니다.",
    tags=["건강 기록"]
)
def create_event(event: EventIn, request: Request):
    user = require_session_user(request)
    # AWS Ubuntu 서버의 기본 시간은 UTC이므로, 사용자에게 보이는 기록 시각은 한국 시간으로 저장합니다.
    occurred_at = event.occurred_at.strip() or datetime.now(KST).isoformat(timespec="minutes")

    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO events (user_id, occurred_at, raw_text, event_type)
        VALUES (?, ?, ?, ?)
        """,
        (user["id"], occurred_at, event.raw_text.strip(), event.event_type.strip() or "기타")
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM events WHERE id = ? AND user_id = ?",
        (cursor.lastrowid, user["id"])
    ).fetchone()
    connection.close()
    return row_to_event(row)


@app.get(
    "/events",
    summary="생활 이벤트 조회",
    description="로그인한 사용자의 최근 생활 이벤트를 조회합니다.",
    tags=["건강 기록"]
)
def get_events(request: Request, limit: int = 30):
    user = require_session_user(request)
    limit = max(1, min(limit, 100))
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM events WHERE user_id = ? ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (user["id"], limit)
    ).fetchall()
    connection.close()
    return {"count": len(rows), "events": [row_to_event(row) for row in rows]}


@app.delete(
    "/events/{event_id}",
    summary="생활 이벤트 삭제",
    description="로그인한 사용자의 생활 이벤트 하나를 삭제합니다.",
    tags=["건강 기록"]
)
def delete_event(event_id: int, request: Request):
    user = require_session_user(request)
    connection = get_connection()
    row = connection.execute(
        "SELECT * FROM events WHERE id = ? AND user_id = ?",
        (event_id, user["id"])
    ).fetchone()
    if row is None:
        connection.close()
        raise HTTPException(status_code=404, detail="해당 이벤트를 찾을 수 없습니다.")
    connection.execute(
        "DELETE FROM events WHERE id = ? AND user_id = ?",
        (event_id, user["id"])
    )
    connection.commit()
    connection.close()
    return {"message": "이벤트가 삭제되었습니다.", "event": row_to_event(row)}


@app.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
def welcome_page(request: Request):
    if get_session_user(request) is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    return HTMLResponse("""
    <!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>내 몸의 실험실</title>
    <style>
    *{box-sizing:border-box}body{margin:0;background:#f7fbf8;color:#18342d;font-family:Arial,'Malgun Gothic',sans-serif}.wrap{width:min(1120px,calc(100% - 36px));margin:auto;padding:28px 0 70px}.nav{display:flex;justify-content:space-between;align-items:center}.brand{font-weight:800;font-size:20px}.brand span{color:#19a974}.nav a{margin-left:16px;color:#47716a;text-decoration:none;font-weight:700}.hero{margin-top:42px;padding:68px 58px;border-radius:32px;background:linear-gradient(135deg,#d9f7e8,#fff3dc);display:grid;grid-template-columns:1.1fr .9fr;gap:30px;overflow:hidden;position:relative}.eyebrow{color:#169b68;font-weight:800;letter-spacing:.08em}.hero h1{font-size:clamp(42px,7vw,78px);line-height:1.05;margin:18px 0;color:#163a30;letter-spacing:-.07em}.hero p{font-size:19px;line-height:1.7;color:#53736a;max-width:570px}.cta{display:inline-block;margin-top:20px;padding:15px 24px;background:#1fae76;color:white;border-radius:14px;text-decoration:none;font-weight:800;box-shadow:0 12px 24px #1fae7633}.visual{display:flex;align-items:center;justify-content:center;font-size:150px;filter:drop-shadow(0 20px 18px #669f8235)}.features{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px}.feature{padding:24px;border-radius:22px;background:white;border:1px solid #e2eee8;box-shadow:0 12px 30px #4d866c12}.feature b{font-size:18px}.feature p{color:#6a837b;line-height:1.6}.note{margin-top:22px;color:#789088;font-size:13px}@media(max-width:720px){.hero{grid-template-columns:1fr;padding:40px 28px}.visual{font-size:100px}.features{grid-template-columns:1fr}.nav a{margin-left:8px;font-size:13px}}
    </style></head><body><main class="wrap"><nav class="nav"><div class="brand">🌿 <span>내 몸의 실험실</span></div><div><a href="/login">로그인</a><a href="/signup">시작하기</a></div></nav><section class="hero"><div><div class="eyebrow">BODY LAB</div><h1>작은 사건이<br>나를 설명해요.</h1><p>커피 한 잔, 늦은 잠, 운동 한 번.<br>생활 속 순간을 가볍게 기록하고 나만의 몸 반응을 발견해보세요.</p><a class="cta" href="/signup">내 실험실 만들기 →</a></div><div class="visual">🪴</div></section><section class="features"><article class="feature"><b>⚡ 바로 기록</b><p>기억이 사라지기 전에 생활 이벤트를 한 줄로 남겨요.</p></article><article class="feature"><b>🧪 나만의 실험</b><p>남과 비교하지 않고 어제의 나와 생활 습관을 비교해요.</p></article><article class="feature"><b>🔐 내 데이터는 나만</b><p>로그인한 사용자별로 건강 기록과 이벤트를 분리해서 보관해요.</p></article></section><p class="note">이 서비스의 건강 분류는 학습용 참고 정보이며 의학적 진단을 대신하지 않습니다.</p></main></body></html>
    """)


@app.get("/app", include_in_schema=False)
def app_entry(request: Request):
    if get_session_user(request) is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/welcome", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    if get_session_user(request) is None:
        return RedirectResponse(url="/login", status_code=303)
    return """
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>마이 헬스 로그</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;500;700&display=swap');

            :root {
                --ink: #18221f;
                --muted: #71807a;
                --purple: #1fae76;
                --purple-dark: #137653;
                --lavender: #f1f7f4;
                --peach: #faf5ee;
                --mint: #eaf7f0;
                --yellow: #f8f6ee;
                --white: #ffffff;
                --shadow: 0 16px 40px rgba(29, 57, 46, 0.08);
            }

            * { box-sizing: border-box; }

            body {
                margin: 0;
                color: var(--ink);
                font-family: 'Noto Sans KR', sans-serif;
                background: #f6f8f7;
            }

            .wrap { width: min(1080px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 60px; }

            .hero {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 24px;
                padding: 34px 38px;
                border-radius: 24px;
                color: white;
                background: #17251f;
                box-shadow: var(--shadow);
                overflow: hidden;
                position: relative;
            }

            .hero::after {
                content: '✦';
                position: absolute;
                right: 30px;
                top: 16px;
                color: #ffffff;
                font-size: 42px;
                transform: rotate(18deg);
            }

            h1, h2, h3 { margin: 0; font-weight: 750; letter-spacing: -.04em; }
            h1 { font-size: clamp(32px, 5vw, 52px); }
            h2 { font-size: 23px; }
            .hero p { margin: 10px 0 0; color: #aebdb6; font-size: 15px; }
            .mascot { font-size: 52px; color: #7ee0b1; }
            .hero-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 12px; position: relative; z-index: 1; }
            .logout-button { width: auto; margin: 0; padding: 9px 14px; color: #18342b; background: #fff; box-shadow: none; font-size: 13px; }
            .logout-button:hover { background: #fff; box-shadow: 0 8px 18px rgba(102, 85, 216, .16); }
            .quick-event { margin-top: 22px; background: #fff; }
            .event-form { display: grid; grid-template-columns: 150px 1fr; gap: 10px; align-items: center; }
            .event-form select { border: 1px solid #e6e1f6; border-radius: 13px; padding: 11px 12px; color: var(--ink); background: #fff; font: inherit; }
            .event-message { min-height: 20px; margin-top: 10px; color: var(--purple-dark); font-size: 13px; }
            .event-list { display: grid; gap: 8px; margin-top: 12px; }
            .event-item { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 11px 13px; border-radius: 13px; background: rgba(255,255,255,.8); border: 1px solid #e7f1eb; font-size: 13px; }
            .event-item small { color: var(--muted); white-space: nowrap; }
            .event-delete { width: auto !important; margin: 0 !important; padding: 5px 8px !important; border: 0; background: transparent; color: #b7aebe; box-shadow: none !important; font-size: 12px !important; }
            .event-delete:hover { color: #d45748; background: transparent; }

            .layout { display: grid; grid-template-columns: 360px 1fr; gap: 22px; margin-top: 22px; }
            .card { background: #fff; border: 1px solid #e5ebe8; border-radius: 20px; padding: 24px; box-shadow: var(--shadow); }
            .card-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
            .card-title span { font-size: 23px; }

            label { display: block; margin: 13px 0 6px; color: var(--muted); font-size: 13px; font-weight: 700; }
            input, textarea {
                width: 100%;
                border: 1px solid #dfe8e3;
                border-radius: 10px;
                padding: 11px 12px;
                color: var(--ink);
                background: #fff;
                font: inherit;
                outline: none;
                transition: .2s;
            }
            input:focus, textarea:focus { border-color: var(--purple); box-shadow: 0 0 0 4px #dff4e9; }
            .two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
            textarea { min-height: 70px; resize: vertical; }
            button {
                width: 100%;
                border: 0;
                border-radius: 10px;
                padding: 13px 16px;
                margin-top: 18px;
                color: white;
                background: #1fae76;
                box-shadow: 0 8px 16px rgba(31, 174, 118, .18);
                cursor: pointer;
                font: 700 15px 'Noto Sans KR', sans-serif;
                transition: transform .2s, box-shadow .2s;
            }
            button:hover { transform: translateY(-1px); box-shadow: 0 10px 20px rgba(31, 174, 118, .25); }

            .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
            .metric { min-height: 118px; border-radius: 18px; padding: 16px; }
            .metric:nth-child(1) { background: var(--lavender); }
            .metric:nth-child(2) { background: var(--peach); }
            .metric:nth-child(3) { background: var(--mint); }
            .metric:nth-child(4) { background: var(--yellow); }
            .metric .emoji { font-size: 24px; }
            .metric .label { margin-top: 9px; color: var(--muted); font-size: 12px; }
            .metric .value { margin-top: 3px; font-size: 25px; font-weight: 750; }

            .records { margin-top: 22px; }
            .calendar-card { margin-top: 22px; }
            .calendar-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
            .calendar-toolbar strong { font-size: 18px; }
            .calendar-nav { display: flex; gap: 6px; }
            .calendar-nav button { width: auto; margin: 0; padding: 7px 11px; color: var(--ink); background: #eef6f1; box-shadow: none; font-size: 13px; }
            .calendar-nav button:hover { background: #dff0e7; box-shadow: none; }
            .calendar-week, .calendar-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; }
            .calendar-week { margin-bottom: 5px; color: var(--muted); text-align: center; font-size: 12px; font-weight: 700; }
            .calendar-day { min-height: 64px; margin: 0; padding: 8px 4px; border: 1px solid #e5ebe8; border-radius: 10px; color: var(--ink); background: #fff; box-shadow: none; font-size: 13px; }
            .calendar-day:hover { transform: none; border-color: var(--purple); background: #f3fbf6; box-shadow: none; }
            .calendar-day.selected { border: 2px solid var(--purple); background: #eaf7f0; }
            .calendar-day.today { color: var(--purple-dark); font-weight: 700; }
            .calendar-day.empty-day { visibility: hidden; }
            .calendar-counts { display: flex; justify-content: center; gap: 3px; margin-top: 7px; color: var(--purple-dark); font-size: 10px; }
            .calendar-detail { margin-top: 16px; padding-top: 15px; border-top: 1px solid #e5ebe8; }
            .calendar-detail h3 { margin-bottom: 10px; font-size: 16px; }
            .calendar-detail-row { padding: 8px 0; border-bottom: 1px solid #f0f3f1; font-size: 13px; }
            .calendar-detail-row small { display: block; margin-top: 3px; color: var(--muted); }
            .record { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 17px 0; border-bottom: 1px solid #f0edf8; }
            .record:last-child { border-bottom: 0; }
            .record-main { min-width: 0; }
            .record-date { font-weight: 700; }
            .record-meta { margin-top: 5px; color: var(--muted); font-size: 13px; }
            .badges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
            .badge { padding: 6px 9px; border-radius: 999px; background: #f1efff; color: var(--purple-dark); font-size: 12px; font-weight: 700; white-space: nowrap; }
            .badge.warn { background: #ffe8e2; color: #d45748; }
            .empty { padding: 44px 10px; text-align: center; color: var(--muted); }
            .message { min-height: 22px; margin-top: 12px; color: var(--purple-dark); font-size: 13px; text-align: center; }
            .edit-tools { margin-bottom: 18px; padding: 14px; border-radius: 16px; background: #f8f6ff; border: 1px dashed #d8d1ff; }
            .edit-tools label { margin-top: 0; }
            .edit-tools .two { align-items: end; }
            .edit-tools button, .edit-button { width: auto; margin-top: 0; padding: 9px 13px; font-size: 13px; box-shadow: none; }
            .edit-button { border: 0; border-radius: 10px; color: var(--purple-dark); background: #eeeaff; cursor: pointer; font-weight: 700; }
            .edit-button:hover { background: #e2dcff; }
            .cancel-button { display: none; margin: 8px auto 0; border: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; }

            @media (max-width: 820px) {
                .layout { grid-template-columns: 1fr; }
                .summary { grid-template-columns: repeat(2, 1fr); }
                .event-form { grid-template-columns: 1fr; }
            }
            @media (max-width: 520px) {
                .wrap { width: min(100% - 20px, 1080px); padding-top: 14px; }
                .hero { padding: 25px; }
                .mascot { font-size: 62px; }
                .record { align-items: flex-start; flex-direction: column; }
                .badges { justify-content: flex-start; }
            }
        </style>
    </head>
    <body>
        <main class="wrap">
            <section class="hero">
                <div>
                    <h1>마이 헬스 로그</h1>
                    <p>오늘의 나를 다정하게 기록해요 ♡</p>
                </div>
                <div class="hero-actions">
                    <button id="logout-button" class="logout-button" type="button">로그아웃</button>
                    <div class="mascot">✦</div>
                </div>
            </section>

            <section class="card quick-event">
                <div class="card-title"><div><h2>지금 무슨 일이 있었나요?</h2><div style="margin-top:5px;color:var(--muted);font-size:13px">커피 한 잔도, 늦은 잠도 나를 이해하는 단서가 돼요.</div></div><span>⚡</span></div>
                <form id="event-form" class="event-form">
                    <select id="event-type" aria-label="이벤트 종류">
                        <option>음료·카페인</option><option>식사</option><option>운동</option><option>수면</option><option>기분·스트레스</option><option>기타</option>
                    </select>
                    <input id="event-text" type="text" maxlength="500" placeholder="예: 아이스아메리카노 마셨어" required>
                </form>
                <button id="event-save" type="submit" form="event-form">이벤트 저장하기</button>
                <div id="event-message" class="event-message"></div>
                <div id="event-list" class="event-list"></div>
            </section>

            <section class="card calendar-card">
                <div class="card-title"><div><h2>날짜별 기록 보기</h2><div style="margin-top:5px;color:var(--muted);font-size:13px">날짜를 클릭하면 그날의 건강 기록과 생활 이벤트를 확인할 수 있어요.</div></div><span>▦</span></div>
                <div class="calendar-toolbar"><strong id="calendar-month"></strong><div class="calendar-nav"><button id="prev-month" type="button">‹ 이전</button><button id="today-month" type="button">오늘</button><button id="next-month" type="button">다음 ›</button></div></div>
                <div class="calendar-week"><span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span></div>
                <div id="calendar-grid" class="calendar-grid"></div>
                <div id="calendar-detail" class="calendar-detail"></div>
            </section>

            <div class="layout">
                <section class="card">
                    <div class="card-title"><h2>건강 기록하기</h2><span>📝</span></div>
                    <div class="edit-tools">
                        <label for="record-id">기존 기록 수정하기</label>
                        <div class="two">
                            <input id="record-id" type="number" min="1" placeholder="기록 번호 입력">
                            <button id="load-record" type="button">불러오기 🔎</button>
                        </div>
                        <div style="margin-top:7px;color:#827b9d;font-size:12px">기록 번호를 입력하면 현재 저장된 내용이 아래에 채워져요.</div>
                    </div>
                    <form id="record-form">
                        <label for="date">측정 날짜</label>
                        <input id="date" type="date" required>

                        <div class="two">
                            <div><label for="weight">몸무게 (kg)</label><input id="weight" type="number" step="0.1" min="0.1" required></div>
                            <div><label for="height">키 (cm)</label><input id="height" type="number" step="0.1" min="0.1" required></div>
                        </div>

                        <div class="two">
                            <div><label for="systolic">수축기 혈압</label><input id="systolic" type="number" min="1" required></div>
                            <div><label for="diastolic">이완기 혈압</label><input id="diastolic" type="number" min="1" required></div>
                        </div>

                        <label for="blood_sugar">공복 혈당 (mg/dL)</label>
                        <input id="blood_sugar" type="number" min="1" required>

                        <div class="two">
                            <div><label for="steps">걸음 수</label><input id="steps" type="number" min="0" value="0"></div>
                            <div><label for="sleep_hours">수면 시간</label><input id="sleep_hours" type="number" step="0.1" min="0" value="0"></div>
                        </div>

                        <label for="memo">메모</label>
                        <textarea id="memo" placeholder="오늘의 컨디션은 어땠나요?"></textarea>
                        <button type="submit">기록 저장하기 ✨</button>
                        <button id="cancel-edit" class="cancel-button" type="button">수정 취소하고 새 기록 만들기</button>
                        <div id="message" class="message"></div>
                    </form>
                </section>

                <section>
                    <div class="card">
                        <div class="card-title"><h2>나의 건강 한눈에 보기</h2><span>🌷</span></div>
                        <div id="summary" class="summary"></div>
                    </div>

                    <div class="card records">
                        <div class="card-title"><h2>최근 기록</h2><span>🌿</span></div>
                        <div id="records"></div>
                    </div>
                </section>
            </div>
        </main>

        <script>
            document.getElementById('logout-button').addEventListener('click', async () => {
                await fetch('/auth/logout', { method: 'POST' });
                location.href = '/login';
            });

            const eventForm = document.getElementById('event-form');
            const eventText = document.getElementById('event-text');
            const eventType = document.getElementById('event-type');
            const eventMessage = document.getElementById('event-message');
            const eventList = document.getElementById('event-list');
            const calendarMonth = document.getElementById('calendar-month');
            const calendarGrid = document.getElementById('calendar-grid');
            const calendarDetail = document.getElementById('calendar-detail');
            let calendarDate = new Date();
            let calendarRecords = [];
            let calendarEvents = [];

            function localDateKey(date = new Date()) {
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            }

            function renderCalendar() {
                const year = calendarDate.getFullYear();
                const month = calendarDate.getMonth();
                calendarMonth.textContent = `${year}년 ${month + 1}월`;
                calendarGrid.innerHTML = '';
                const firstDay = new Date(year, month, 1).getDay();
                const lastDate = new Date(year, month + 1, 0).getDate();
                const selected = calendarDetail.dataset.date || localDateKey();
                for (let i = 0; i < firstDay; i++) {
                    const blank = document.createElement('div');
                    blank.className = 'calendar-day empty-day';
                    calendarGrid.appendChild(blank);
                }
                for (let day = 1; day <= lastDate; day++) {
                    const key = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                    const recordCount = calendarRecords.filter(record => record.date === key).length;
                    const eventCount = calendarEvents.filter(event => event.occurred_at.startsWith(key)).length;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = `calendar-day ${key === selected ? 'selected' : ''} ${key === localDateKey() ? 'today' : ''}`;
                    button.innerHTML = `<span>${day}</span><span class="calendar-counts">${recordCount ? `♥ ${recordCount}` : ''}${eventCount ? ` · ✦ ${eventCount}` : ''}</span>`;
                    button.addEventListener('click', () => selectCalendarDate(key));
                    calendarGrid.appendChild(button);
                }
            }

            function selectCalendarDate(key) {
                calendarDetail.dataset.date = key;
                renderCalendar();
                const records = calendarRecords.filter(record => record.date === key);
                const events = calendarEvents.filter(event => event.occurred_at.startsWith(key));
                calendarDetail.innerHTML = `<h3>${key} 기록</h3>${records.length ? records.map(record => `<div class="calendar-detail-row">건강 기록 · BMI ${escapeHtml(record.bmi)} · ${escapeHtml(record.weight)}kg<small>혈압 ${escapeHtml(record.systolic)}/${escapeHtml(record.diastolic)} · 걸음 ${escapeHtml(record.steps)} · ${escapeHtml(record.memo || '메모 없음')}</small></div>`).join('') : ''}${events.length ? events.map(event => `<div class="calendar-detail-row">${escapeHtml(event.event_type)} · ${escapeHtml(event.raw_text)}<small>${escapeHtml(event.occurred_at.replace('T', ' '))}</small></div>`).join('') : ''}${!records.length && !events.length ? '<div class="empty" style="padding:12px 0">이 날짜에는 저장된 기록이 없어요.</div>' : ''}`;
            }

            document.getElementById('prev-month').addEventListener('click', () => { calendarDate.setMonth(calendarDate.getMonth() - 1); renderCalendar(); });
            document.getElementById('next-month').addEventListener('click', () => { calendarDate.setMonth(calendarDate.getMonth() + 1); renderCalendar(); });
            document.getElementById('today-month').addEventListener('click', () => { calendarDate = new Date(); selectCalendarDate(localDateKey()); });

            function renderEvents(events) {
                eventList.innerHTML = '';
                if (!events.length) {
                    eventList.innerHTML = '<div class="empty" style="padding:18px 0">아직 빠른 이벤트 기록이 없어요.</div>';
                    return;
                }
                events.forEach(event => {
                    const item = document.createElement('div');
                    item.className = 'event-item';
                    const text = document.createElement('div');
                    text.textContent = `${event.event_type} · ${event.raw_text}`;
                    const meta = document.createElement('small');
                    meta.textContent = event.occurred_at.replace('T', ' ');
                    const remove = document.createElement('button');
                    remove.className = 'event-delete';
                    remove.type = 'button';
                    remove.textContent = '삭제';
                    remove.addEventListener('click', async () => {
                        const response = await fetch(`/events/${event.id}`, { method: 'DELETE' });
                        if (response.ok) loadEvents();
                    });
                    item.append(text, meta, remove);
                    eventList.appendChild(item);
                });
            }

            async function loadEvents() {
                const response = await fetch('/events?limit=8');
                if (!response.ok) return;
                const data = await response.json();
                renderEvents(data.events);
                const calendarResponse = await fetch('/events?limit=100');
                if (calendarResponse.ok) {
                    calendarEvents = (await calendarResponse.json()).events;
                    renderCalendar();
                    selectCalendarDate(calendarDetail.dataset.date || localDateKey());
                }
            }

            eventForm.addEventListener('submit', async event => {
                event.preventDefault();
                eventMessage.textContent = '저장 중이에요...';
                const response = await fetch('/events', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({raw_text: eventText.value, event_type: eventType.value})
                });
                if (!response.ok) {
                    eventMessage.textContent = '이벤트를 저장하지 못했어요.';
                    return;
                }
                eventText.value = '';
                eventMessage.textContent = '기록했어요. 나중에 나만의 패턴을 찾아볼게요 ✨';
                loadEvents();
            });

            loadEvents();

            const form = document.getElementById('record-form');
            const message = document.getElementById('message');
            const dateInput = document.getElementById('date');
            const recordIdInput = document.getElementById('record-id');
            const loadRecordButton = document.getElementById('load-record');
            const submitButton = form.querySelector('button[type="submit"]');
            const cancelEditButton = document.getElementById('cancel-edit');
            let editingId = null;
            dateInput.value = new Date().toISOString().slice(0, 10);

            function escapeHtml(value) {
                return String(value ?? '').replace(/[&<>'"]/g, char => ({
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
                }[char]));
            }

            function badge(text, warning = false) {
                return `<span class="badge ${warning ? 'warn' : ''}">${escapeHtml(text)}</span>`;
            }

            function renderSummary(record) {
                const summary = document.getElementById('summary');
                if (!record) {
                    summary.innerHTML = '<div class="empty" style="grid-column:1/-1">아직 기록이 없어요. 첫 기록을 남겨보세요 🐰</div>';
                    return;
                }

                summary.innerHTML = `
                    <div class="metric"><div class="emoji">⚖️</div><div class="label">BMI</div><div class="value">${escapeHtml(record.bmi)} <small>${escapeHtml(record.bmi_category)}</small></div></div>
                    <div class="metric"><div class="emoji">💗</div><div class="label">혈압</div><div class="value" style="font-size:20px">${escapeHtml(record.bp_category)}</div></div>
                    <div class="metric"><div class="emoji">🍬</div><div class="label">혈당</div><div class="value" style="font-size:20px">${escapeHtml(record.sugar_category)}</div></div>
                    <div class="metric"><div class="emoji">✨</div><div class="label">오늘의 경고</div><div class="value">${record.warnings.length}개</div></div>
                `;
            }

            function renderRecords(payload) {
                const target = document.getElementById('records');
                if (!payload.records.length) {
                    target.innerHTML = '<div class="empty">아직 저장된 기록이 없어요 🌱</div>';
                    renderSummary(null);
                    return;
                }

                const sorted = [...payload.records].sort((a, b) => b.id - a.id);
                renderSummary(sorted[0]);
                target.innerHTML = sorted.map(record => `
                    <div class="record">
                        <div class="record-main">
                            <div class="record-date">${escapeHtml(record.date)} <span style="color:#aaa;font-size:12px">#${escapeHtml(record.id)}</span></div>
                            <div class="record-meta">몸무게 ${escapeHtml(record.weight)}kg · 혈압 ${escapeHtml(record.systolic)}/${escapeHtml(record.diastolic)} · 혈당 ${escapeHtml(record.blood_sugar)}</div>
                        </div>
                        <div class="badges">
                            ${badge('BMI ' + record.bmi_category)}
                            ${badge('혈압 ' + record.bp_category)}
                            ${badge('혈당 ' + record.sugar_category)}
                            ${record.warnings.map(warning => badge(warning, true)).join('')}
                            <button class="edit-button" type="button" onclick="startEdit(${record.id})">수정하기</button>
                        </div>
                    </div>
                `).join('');
            }

            async function loadRecords() {
                const response = await fetch('/records');
                const payload = await response.json();
                calendarRecords = payload.records;
                renderRecords(payload);
                renderCalendar();
                selectCalendarDate(calendarDetail.dataset.date || localDateKey());
            }

            function fillForm(record) {
                dateInput.value = record.date;
                document.getElementById('weight').value = record.weight;
                document.getElementById('height').value = record.height;
                document.getElementById('systolic').value = record.systolic;
                document.getElementById('diastolic').value = record.diastolic;
                document.getElementById('blood_sugar').value = record.blood_sugar;
                document.getElementById('steps').value = record.steps;
                document.getElementById('sleep_hours').value = record.sleep_hours;
                document.getElementById('memo').value = record.memo;
                editingId = record.id;
                recordIdInput.value = record.id;
                submitButton.textContent = '수정 내용 저장하기 💜';
                cancelEditButton.style.display = 'block';
                message.textContent = `#${record.id} 기록을 불러왔어요. 아래 내용을 수정해 보세요!`;
                form.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            async function startEdit(id) {
                recordIdInput.value = id;
                await loadRecord();
            }

            async function loadRecord() {
                const id = Number(recordIdInput.value);
                if (!id) {
                    message.textContent = '수정할 기록 번호를 입력해 주세요.';
                    return;
                }

                const response = await fetch(`/records/${id}`);
                if (!response.ok) {
                    message.textContent = '해당 기록을 찾을 수 없어요.';
                    return;
                }

                fillForm(await response.json());
            }

            loadRecordButton.addEventListener('click', loadRecord);

            cancelEditButton.addEventListener('click', () => {
                editingId = null;
                recordIdInput.value = '';
                form.reset();
                dateInput.value = new Date().toISOString().slice(0, 10);
                document.getElementById('steps').value = 0;
                document.getElementById('sleep_hours').value = 0;
                submitButton.textContent = '새 기록 저장하기 ✨';
                cancelEditButton.style.display = 'none';
                message.textContent = '새 기록을 입력할 수 있어요.';
            });

            form.addEventListener('submit', async event => {
                event.preventDefault();
                message.textContent = '저장 중이에요...';

                const payload = {
                    date: dateInput.value,
                    weight: Number(document.getElementById('weight').value),
                    height: Number(document.getElementById('height').value),
                    systolic: Number(document.getElementById('systolic').value),
                    diastolic: Number(document.getElementById('diastolic').value),
                    blood_sugar: Number(document.getElementById('blood_sugar').value),
                    steps: Number(document.getElementById('steps').value || 0),
                    sleep_hours: Number(document.getElementById('sleep_hours').value || 0),
                    memo: document.getElementById('memo').value
                };

                const url = editingId ? `/records/${editingId}` : '/records';
                const method = editingId ? 'PUT' : 'POST';
                const response = await fetch(url, {
                    method,
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    message.textContent = '입력값을 다시 확인해 주세요 🥲';
                    return;
                }

                message.textContent = '건강 기록을 저장했어요! 💜';
                editingId = null;
                form.reset();
                dateInput.value = new Date().toISOString().slice(0, 10);
                document.getElementById('steps').value = 0;
                document.getElementById('sleep_hours').value = 0;
                recordIdInput.value = '';
                submitButton.textContent = '새 기록 저장하기 ✨';
                cancelEditButton.style.display = 'none';
                await loadRecords();
            });

            loadRecords().catch(() => {
                document.getElementById('records').innerHTML = '<div class="empty">기록을 불러오지 못했어요.</div>';
            });
        </script>
    </body>
    </html>
    """
