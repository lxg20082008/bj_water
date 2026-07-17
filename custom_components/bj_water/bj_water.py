"""北京自来水 API 数据获取模块"""
import asyncio
import json
from datetime import datetime

import async_timeout

from .const import LOGGER

SERVICE_HOST = "https://www.bjwatergroupkf.com.cn"
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30


class InvalidData(Exception):
    """无效数据异常"""
    pass


class BJWater:
    """北京自来水数据获取类"""

    def __init__(self, session, user_code) -> None:
        self._session = session
        self.user_code = user_code
        self.bill_cycle = set()
        self.info = {
            "cycle": {},
            "user_code": "",
            "meter_value": [],
            "monthlist": [],
            "yearlist": [],
            "daylist": [],
        }

    async def _request_with_retry(self, url, params):
        """发送 HTTP GET 请求，带重试和超时"""
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                async with async_timeout.timeout(REQUEST_TIMEOUT):
                    response = await self._session.get(url=url, params=params)
                    if response.status == 200:
                        return json.loads(await response.read())
                    last_exception = InvalidData(
                        f"HTTP {response.status} for {url}"
                    )
            except asyncio.TimeoutError:
                last_exception = InvalidData(f"请求超时: {url}")
                LOGGER.warning("请求超时，第 %d/%d 次重试: %s", attempt + 1, MAX_RETRIES, url)
            except InvalidData:
                raise
            except Exception as e:
                last_exception = e
                LOGGER.warning("请求异常，第 %d/%d 次重试: %s", attempt + 1, MAX_RETRIES, str(e))
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
        raise last_exception

    async def get_bill_cycle_range(self):
        """获取账单周期列表"""
        LOGGER.info("get_bill_cycle_range user code: %s", self.user_code)
        bill_month_api = f"{SERVICE_HOST}/api/member/bizMyWater/getPcMonthsAndYears"
        json_body = await self._request_with_retry(bill_month_api, {"userCode": self.user_code})
        LOGGER.info("get_bill_cycle_range response: %s", json_body)

        data = json_body.get("data", {})
        if "months" not in data or len(data["months"]) == 0:
            raise InvalidData(f"未查到账单周期，请检查户号: {self.user_code}!")

        # 获取全部可用月份（不再限 6 个月）
        bill_list = sorted(data["months"], reverse=True)
        for bill in bill_list:
            cycle_date = datetime.strptime(bill, "%Y年%m月").strftime("%Y-%m")
            self.bill_cycle.add(cycle_date)
            self.info["cycle"][cycle_date] = {
                "fee": {
                    "pay": 0,
                    "date": cycle_date,
                    "amount": 0,
                    "szyf": 0,
                    "wsf": 0,
                    "sf": 0,
                }
            }
        self.info["user_code"] = self.user_code
        LOGGER.info("get_bill_cycle_range end, %d cycles", len(self.bill_cycle))
        return self.bill_cycle

    async def get_payment_bill(self):
        """获取缴费记录"""
        payment_api = f"{SERVICE_HOST}/api/member/bizMyWater/pcPaymentRecord"
        json_body = await self._request_with_retry(payment_api, {"userCode": self.user_code})
        LOGGER.info("get_payment_bill: %s", json_body)

        bill_list = json_body.get("data", [])
        if len(bill_list) == 0:
            LOGGER.warning("未查询到缴费记录")
            return

        for index, bill in enumerate(bill_list):
            cycle_date = datetime.strptime(bill["billDate"], "%Y年%m月").strftime("%Y-%m")
            if cycle_date in self.bill_cycle:
                # 用 update 保留已有的 meter 等字段
                existing = self.info["cycle"].get(cycle_date, {})
                existing["index"] = index
                existing["fee"] = {
                    "pay": 1,
                    "date": datetime.strptime(bill["date"], "%Y.%m.%d").strftime("%Y-%m-%d"),
                    "amount": bill["amount"],
                    "szyf": bill["szyf"],
                    "wsf": bill["wsf"],
                    "sf": bill["sf"],
                }
                self.info["cycle"][cycle_date] = existing
        LOGGER.info("get_payment_bill end")

    async def get_monthly_bill(self, bill_cycle):
        """获取单个月份的账单详情"""
        monthly_api = f"{SERVICE_HOST}/api/member/bizMyWater/getPcMonthlyBill"
        # bill_cycle 格式: "2026-06", 需要转成 "2026年06月"
        bill_date_str = bill_cycle.replace("-", "年") + "月"
        params = {"userCode": self.user_code, "billDate": bill_date_str}

        try:
            json_body = await self._request_with_retry(monthly_api, params)
        except InvalidData as e:
            LOGGER.warning("获取月度账单失败 %s: %s", bill_cycle, e)
            return self.info

        detail_data = json_body.get("data", {})
        LOGGER.info("get_monthly_bill %s: code=%s", bill_cycle, json_body.get("code"))

        # 该月份暂无账单数据（如 owe=0 表示欠费为空，即尚未出账）
        if not detail_data.get("endValue") or detail_data.get("total") is None:
            LOGGER.warning("账单周期 %s 暂无数据", bill_cycle)
            return self.info

        # 始终更新 meter 数据（无论是否已缴费）
        self.info["cycle"][bill_cycle]["meter"] = {
            "usage": detail_data.get("total", 0),
            "value": [detail_data.get("endValue", "").split("/")],
        }

        # 仅在缴费记录未覆盖时从月度账单填充费用信息
        cycle_entry = self.info["cycle"].get(bill_cycle, {})
        if cycle_entry.get("fee", {}).get("pay") == 0:
            self.info["cycle"][bill_cycle]["index"] = bill_cycle
            self.info["cycle"][bill_cycle]["fee"] = {
                "pay": 0,
                "date": bill_cycle,
                "amount": detail_data.get("amount", 0),
                "szyf": detail_data.get("taxFee", {}).get("amount", 0),
                "wsf": detail_data.get("waterborneFee", {}).get("amount", 0),
                "sf": detail_data.get("firstStep", {}).get("amount", 0),
            }

        # 更新阶梯总用量（取所有月份中最大值）
        grand_total = int(detail_data.get("grandTotal", 0))
        if "total_usage" not in self.info or self.info["total_usage"] < grand_total:
            self.info["total_usage"] = grand_total

        # 更新水表读数
        end_value_str = detail_data.get("endValue", "")
        if end_value_str:
            meter_values = end_value_str.split("/")
            for i in range(len(meter_values)):
                try:
                    mv_int = int(meter_values[i])
                except ValueError:
                    continue
                if len(self.info["meter_value"]) <= i:
                    self.info["meter_value"].append({i: mv_int})
                elif i < len(self.info["meter_value"]):
                    existing = self.info["meter_value"][i].get(i)
                    if existing is None or existing < mv_int:
                        self.info["meter_value"][i][i] = mv_int

        # 更新阶梯和单价信息（取最新可用月份）
        step_left = detail_data.get("stepLeft", {})
        if step_left:
            self.info["first_step_left"] = int(step_left.get("fist", 0))
            self.info["second_step_left"] = int(step_left.get("second", 0))

        first_step = detail_data.get("firstStep", {})
        if first_step:
            self.info["first_step_price"] = float(first_step.get("price", 0))

        wastewater = detail_data.get("waterborneFee", {})
        if wastewater:
            self.info["wastwater_treatment_price"] = float(wastewater.get("price", 0))

        tax_fee = detail_data.get("taxFee", {})
        if tax_fee:
            self.info["water_tax"] = float(tax_fee.get("price", 0))

        self.info["total_cost"] = (
            self.info.get("water_tax", 0)
            + self.info.get("first_step_price", 0)
            + self.info.get("wastwater_treatment_price", 0)
        )

        return self.info

    def _build_lists(self):
        """从 cycle 数据构建 monthlist/yearlist（兼容 electricity-info-card 格式）"""
        monthlist = []
        year_data = {}

        for cycle_date in sorted(self.bill_cycle, reverse=True):
            cycle_info = self.info["cycle"].get(cycle_date, {})
            fee = cycle_info.get("fee", {})
            meter = cycle_info.get("meter", {})

            month_entry = {
                "month": cycle_date,
                "monthWaterNum": meter.get("usage", 0) or 0,
                "monthWaterCost": fee.get("amount", 0) or 0,
            }
            monthlist.append(month_entry)

            # 按年聚合
            year = cycle_date[:4]
            if year not in year_data:
                year_data[year] = {"year": year, "yearWaterNum": 0, "yearWaterCost": 0}
            year_data[year]["yearWaterNum"] += month_entry["monthWaterNum"]
            year_data[year]["yearWaterCost"] += month_entry["monthWaterCost"]

        yearlist = sorted(year_data.values(), key=lambda x: x["year"], reverse=True)
        return monthlist, yearlist

    async def fetch_data(self):
        """获取全部水费数据"""
        await self.get_bill_cycle_range()
        await self.get_payment_bill()
        for bill_date in sorted(self.bill_cycle, reverse=True):
            await self.get_monthly_bill(bill_date)

        monthlist, yearlist = self._build_lists()
        self.info["monthlist"] = monthlist
        self.info["yearlist"] = yearlist
        self.info["daylist"] = []  # 公开 API 不提供日用水数据

        LOGGER.info(
            "fetch_data complete: %d months, %d years",
            len(monthlist),
            len(yearlist),
        )
        return self.info
