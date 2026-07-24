"""Insert two weeks of demo data for one existing user.

This is a one-time local/server utility. It never changes the user's password.
"""

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def calculate_bmi(weight: float, height: float) -> float:
    return round(weight / ((height / 100) ** 2), 2)


def bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "저체중"
    if bmi < 23:
        return "정상"
    if bmi < 25:
        return "과체중"
    return "비만"


def bp_category(systolic: int, diastolic: int) -> str:
    if systolic < 120 and diastolic < 80:
        return "정상"
    if systolic < 140 and diastolic < 90:
        return "주의"
    return "고혈압"


def sugar_category(sugar: int) -> str:
    if sugar < 100:
        return "정상"
    if sugar < 126:
        return "공복혈당장애"
    return "당뇨 의심"


def warnings(bmi: str, bp: str, sugar: str) -> list[str]:
    result = []
    if bmi == "비만":
        result.append("BMI가 비만 범위입니다.")
    if bp == "고혈압":
        result.append("혈압이 고혈압 범위입니다.")
    if sugar == "당뇨 의심":
        result.append("혈당이 당뇨 의심 범위입니다.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--db", default="health_records.db")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    if args.days < 1 or args.days > 31:
        raise SystemExit("days는 1~31 사이여야 합니다.")

    connection = sqlite3.connect(Path(args.db))
    connection.row_factory = sqlite3.Row
    user = connection.execute(
        "SELECT id FROM users WHERE lower(email) = lower(?)", (args.email,)
    ).fetchone()
    if user is None:
        raise SystemExit(f"사용자를 찾을 수 없습니다: {args.email}")

    user_id = user["id"]
    today = datetime.now(KST).date()

    # 같은 이메일에 시드 데이터를 다시 넣어도 중복되지 않도록 표시 문구를 사용합니다.
    connection.execute(
        "DELETE FROM events WHERE user_id = ? AND raw_text LIKE '[데모]%'", (user_id,)
    )
    connection.execute(
        "DELETE FROM records WHERE user_id = ? AND memo LIKE '[데모]%'", (user_id,)
    )

    for offset in range(args.days - 1, -1, -1):
        day = today - timedelta(days=offset)
        weight = round(68.8 + ((args.days - offset) % 5) * 0.25, 1)
        height = 175.0
        systolic = 116 + ((args.days - offset) % 4) * 3
        diastolic = 76 + ((args.days - offset) % 3) * 2
        sugar = 91 + ((args.days - offset) % 5) * 3
        steps = 5200 + ((args.days - offset) % 6) * 850
        sleep = round(6.3 + ((args.days - offset) % 4) * 0.4, 1)
        bmi = calculate_bmi(weight, height)
        bmi_cat = bmi_category(bmi)
        bp_cat = bp_category(systolic, diastolic)
        sugar_cat = sugar_category(sugar)
        connection.execute(
            """
            INSERT INTO records (
                date, weight, height, systolic, diastolic, blood_sugar, steps,
                sleep_hours, memo, bmi, bmi_category, bp_category,
                sugar_category, warnings, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day.isoformat(), weight, height, systolic, diastolic, sugar,
                steps, sleep, "[데모] 2주 습관 데이터", bmi, bmi_cat, bp_cat,
                sugar_cat, json.dumps(warnings(bmi_cat, bp_cat, sugar_cat), ensure_ascii=False),
                user_id,
            ),
        )

        event_time = datetime.combine(day, datetime.min.time(), tzinfo=KST).replace(hour=9, minute=0)
        connection.execute(
            "INSERT INTO events (user_id, occurred_at, raw_text, event_type) VALUES (?, ?, ?, ?)",
            (user_id, event_time.isoformat(timespec="minutes"), "[데모] 아침 식사 후 물 한 잔", "식사·음료"),
        )
        evening_time = event_time.replace(hour=22, minute=30)
        connection.execute(
            "INSERT INTO events (user_id, occurred_at, raw_text, event_type) VALUES (?, ?, ?, ?)",
            (user_id, evening_time.isoformat(timespec="minutes"), "[데모] 오늘은 평소보다 일찍 잠들기", "수면·휴식"),
        )

    connection.commit()
    connection.close()
    print(f"완료: {args.email} 계정에 건강 기록 {args.days}개와 이벤트 {args.days * 2}개를 넣었습니다.")


if __name__ == "__main__":
    main()
