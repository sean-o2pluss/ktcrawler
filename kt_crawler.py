from __future__ import annotations

import csv
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

KT_SHOP_BASE = "https://shop.kt.com"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = BASE_DIR / "subsidy.csv"
OUTPUT_LOG = BASE_DIR / "crawl_log.txt"

MAX_WORKERS = 10
REQUEST_TIMEOUT = 10

# 테스트용
DEFAULT_LIMIT_ONFRM = 5
DEFAULT_HEAD = 100

# 전체 수집용
# DEFAULT_LIMIT_ONFRM = 0
# DEFAULT_HEAD = 0

SUBSIDY_COLS = [
    "prodNo",
    "petNm",
    "hndsetModelId",
    "hndsetModelNm",
    "ofwAmt",
    "realAmt",
    "pplId",
    "pplNm",
    "ktSuprtAmt",
    "spnsrPunoDate",
    "_onfrmCd",
    "_deviceType",
    "_dscnOptnCd",
    "type",
]


if OUTPUT_LOG.exists():
    OUTPUT_LOG.unlink()


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{now}] {message}"

    print(text)

    with OUTPUT_LOG.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


log("작업 시작")

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://shop.kt.com",
    "Referer": "https://shop.kt.com/mobile/mobile.do",
    "X-Requested-With": "XMLHttpRequest",
})

kt_base = KT_SHOP_BASE.rstrip("/")

try:
    response = session.get(f"{kt_base}/mobile/mobile.do", timeout=REQUEST_TIMEOUT)
    log(f"[INIT] mobile.do status={response.status_code}")
except Exception as e:
    log(f"[INIT_ERROR] mobile.do error={type(e).__name__}: {e}")


plan_url = f"{kt_base}/oneMinuteReform/supportAmtChoiceList.json"

payload_combinations = [
    {"pplType": "5G", "pplSelect": "ALL", "deviceType": "HDP"},
    {"pplType": "LTE", "pplSelect": "ALL", "deviceType": "HDP"},
    {"pplType": "LTE", "pplSelect": "ALL", "deviceType": "WATCH"},
    {"pplType": "5G", "pplSelect": "97", "deviceType": "PAD"},
    {"pplType": "5G", "pplSelect": "ALL", "deviceType": "EGG"},
    {"pplType": "5G", "pplSelect": "97", "deviceType": "SMTD"},
    {"pplType": "LTE", "pplSelect": "300", "deviceType": "PAD"},
    {"pplType": "LTE", "pplSelect": "130", "deviceType": "EGG"},
    {"pplType": "LTE", "pplSelect": "300", "deviceType": "SMTD"},
]

base_payload = {
    "sortPpl": "dataDesc",
    "spnsMonsType": "2",
    "pageNo": "0",
}


def fetch_plan(combo):
    payload = {**base_payload, **combo}

    try:
        response = session.post(plan_url, data=payload, timeout=REQUEST_TIMEOUT)
        log(f"[PLAN] combo={combo} status={response.status_code}")

        if response.status_code != 200:
            return []

        rows = response.json().get("punoPplList", []) or []
        log(f"[PLAN_RESULT] combo={combo} count={len(rows)}")

        for row in rows:
            row.update(combo)

        return rows

    except Exception as e:
        log(f"[PLAN_ERROR] combo={combo} error={type(e).__name__}: {e}")
        return []


raw_plans = []

log("plan 조회 시작")

with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(payload_combinations))) as executor:
    futures = [executor.submit(fetch_plan, combo) for combo in payload_combinations]

    for future in as_completed(futures):
        try:
            raw_plans.extend(future.result())
        except Exception as e:
            log(f"[PLAN_FUTURE_ERROR] error={type(e).__name__}: {e}")

log(f"raw_plan_count={len(raw_plans)}")


seen = set()
plans = []

for row in raw_plans:
    onfrm_cd = row.get("onfrmCd")

    if not onfrm_cd or onfrm_cd in seen:
        continue

    seen.add(onfrm_cd)

    cleaned = {}

    for key, value in row.items():
        cleaned[key] = html.unescape(value) if isinstance(value, str) else value

    plans.append(cleaned)

log(f"plan_count={len(plans)}")


onfrm_cd_list = [row["onfrmCd"] for row in plans if row.get("onfrmCd")]
onfrm_cd_list = list(dict.fromkeys(onfrm_cd_list))

if DEFAULT_LIMIT_ONFRM > 0:
    onfrm_cd_list = onfrm_cd_list[:DEFAULT_LIMIT_ONFRM]

log(f"target_onfrm_count={len(onfrm_cd_list)}")


device_type_mapping = {
    "HDP": {"prodNm": "mobile", "prodType": "30"},
    "WATCH": {"prodNm": "Wearable", "prodType": "38"},
    "PAD": {"prodNm": "pad", "prodType": "34"},
    "EGG": {"prodNm": "egg", "prodType": "37"},
    "SMTD": {"prodNm": "etc5g", "prodType": "98"},
}

option_combinations = [
    {"dscnOptnCd": "NT", "sbscTypeCd": "01"},
    {"dscnOptnCd": "MT", "sbscTypeCd": "02"},
    {"dscnOptnCd": "HT", "sbscTypeCd": "04"},
]

common_payload = {
    "sortProd": "oBspnsrPunoDateDesc",
    "spnsMonsType": "undefined",
}

type_map = {
    "NT": "NEW",
    "MT": "MOVE",
    "HT": "CHANGE",
}


def fetch_subsidy(onfrm_cd, device_type, extra_info, dscn_optn_cd, sbsc_type_cd):
    url = f"{kt_base}/mobile/retvSuFuList.json"
    page = 1
    records = []

    while True:
        payload = {
            **common_payload,
            "prdcCd": onfrm_cd,
            "deviceType": device_type,
            "prodNm": extra_info["prodNm"],
            "prodType": extra_info["prodType"],
            "dscnOptnCd": dscn_optn_cd,
            "sbscTypeCd": sbsc_type_cd,
            "pageNo": str(page),
        }

        try:
            response = session.post(url, data=payload, timeout=REQUEST_TIMEOUT)

            log(
                f"[SUBSIDY] onfrmCd={onfrm_cd} device={device_type} "
                f"type={dscn_optn_cd} page={page} status={response.status_code}"
            )

            if response.status_code != 200:
                break

            rows = response.json().get("LIST_DATA", [])

            log(
                f"[SUBSIDY_RESULT] onfrmCd={onfrm_cd} device={device_type} "
                f"type={dscn_optn_cd} page={page} count={len(rows)}"
            )

            if not rows:
                break

            for row in rows:
                row.update({
                    "_onfrmCd": onfrm_cd,
                    "_deviceType": device_type,
                    "_page": page,
                    "_dscnOptnCd": dscn_optn_cd,
                    "_sbscTypeCd": sbsc_type_cd,
                    "type": type_map.get(dscn_optn_cd, "ETC"),
                })

            records.extend(rows)
            page += 1

        except Exception as e:
            log(
                f"[SUBSIDY_ERROR] onfrmCd={onfrm_cd} device={device_type} "
                f"type={dscn_optn_cd} page={page} error={type(e).__name__}: {e}"
            )
            break

    return records


tasks = [
    (
        onfrm_cd,
        device_type,
        extra_info,
        option["dscnOptnCd"],
        option["sbscTypeCd"],
    )
    for onfrm_cd in onfrm_cd_list
    for device_type, extra_info in device_type_mapping.items()
    for option in option_combinations
]

log(f"subsidy_task_count={len(tasks)}")
log("subsidy 조회 시작")

raw_subsidies = []

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = [executor.submit(fetch_subsidy, *task) for task in tasks]

    for future in as_completed(futures):
        try:
            raw_subsidies.extend(future.result())
        except Exception as e:
            log(f"[SUBSIDY_FUTURE_ERROR] error={type(e).__name__}: {e}")

log(f"raw_subsidy_count={len(raw_subsidies)}")


subsidies = []

for record in raw_subsidies:
    item = {}

    for col in SUBSIDY_COLS:
        value = record.get(col)
        item[col] = html.unescape(value) if isinstance(value, str) else value

    date_value = item.get("spnsrPunoDate")

    if isinstance(date_value, str) and len(date_value) >= 8 and date_value[:8].isdigit():
        item["spnsrPunoDate"] = f"{date_value[0:4]}-{date_value[4:6]}-{date_value[6:8]}"

    subsidies.append(item)


if DEFAULT_HEAD > 0:
    subsidies = subsidies[:DEFAULT_HEAD]

log(f"subsidy_count={len(subsidies)}")


with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=SUBSIDY_COLS)
    writer.writeheader()
    writer.writerows(subsidies)

log(f"saved_csv={OUTPUT_CSV}")
log(f"saved_log={OUTPUT_LOG}")
log("작업 완료")
