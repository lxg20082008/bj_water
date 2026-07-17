"""北京水费传感器"""
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .bj_water import BJWater
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL

# ========== 数值型传感器 ==========
SENSORS = {
    "total_usage": {
        "name": "第一阶梯总用量",
        "icon": "hass:water-pump",
        "unit_of_measurement": "m³",
        "attributes": ["last_update"],
        "device_class": SensorDeviceClass.WATER,
        "state_class": SensorStateClass.TOTAL,
    },
    "meter_value": {
        "name": "水表总数",
        "icon": "hass:scale",
        "unit_of_measurement": "m³",
        "attributes": ["last_update"],
        "device_class": SensorDeviceClass.WATER,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "first_step_left": {
        "name": "第一阶梯剩余用量",
        "icon": "hass:water-pump",
        "unit_of_measurement": "m³",
        "device_class": SensorDeviceClass.WATER,
        "attributes": ["last_update"],
    },
    "first_step_price": {
        "name": "第一阶梯水费单价",
        "icon": "hass:currency-cny",
        "unit_of_measurement": "CNY",
    },
    "wastwater_treatment_price": {
        "name": "污水处理费单价",
        "icon": "hass:currency-cny",
        "unit_of_measurement": "CNY",
    },
    "water_tax": {
        "name": "水资源费单价",
        "icon": "hass:currency-cny",
        "unit_of_measurement": "CNY",
    },
    "second_step_left": {
        "name": "第二阶梯剩余用量",
        "icon": "hass:water-pump",
        "unit_of_measurement": "m³",
        "device_class": SensorDeviceClass.WATER,
    },
    "total_cost": {
        "name": "当前水费总单价",
        "icon": "hass:cash-100",
        "unit_of_measurement": "CNY/m³",
        "device_class": SensorDeviceClass.WATER,
    },
}

# ========== 历史费用传感器属性名映射 ==========
HISTORY_FEE_SENSORS = {
    "amount": {"name": "总水费"},
    "szyf": {"name": "水资源费"},
    "wsf": {"name": "污水处理费"},
    "sf": {"name": "水费"},
    "pay": {"name": "缴费状态"},
    "date": {"name": "缴费日期"},
}

HISTORY_USAGE_SENSORS = {
    "usage": {"name": "总用水量"},
    "value": {"name": "水表数"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """配置入口 — 创建所有传感器"""
    sensors_list = []
    config = hass.data[DOMAIN][config_entry.entry_id]
    user_code = config["userCode"]
    api = BJWater(async_create_clientsession(hass), user_code)

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name=DOMAIN,
        update_interval=UPDATE_INTERVAL,
        update_method=api.fetch_data,
    )
    LOGGER.info("async_setup_entry: %s", coordinator)
    await coordinator.async_refresh()
    data = coordinator.data

    # 构建设备信息 — 每个户号一个设备，可分配区域
    device_info = DeviceInfo(
        identifiers={(DOMAIN, user_code)},
        name=f"北京水费 ({user_code})",
        manufacturer="北京自来水集团",
        model="水费账单",
    )

    # 原有的数值型和历史传感器
    for key, value in data.items():
        if key in SENSORS:
            if isinstance(value, list):
                for items in value:
                    for k, v in items.items():
                        sensors_list.append(
                            BJWaterSensor(coordinator, user_code, key, v, device_info, k)
                        )
            else:
                sensors_list.append(
                    BJWaterSensor(coordinator, user_code, key, value, device_info)
                )
        elif key == "cycle":
            for k, v in value.items():
                index = v.get("index", k)
                sensors_list.append(
                    BJWaterHistoryFeeSensor(
                        coordinator, user_code, k, v.get("fee", {}), index, device_info
                    )
                )
                sensors_list.append(
                    BJWaterHistoryUsageSensor(
                        coordinator, user_code, k, v.get("meter", {}), index, device_info
                    )
                )

    # 主信息传感器（兼容 electricity-info-card）
    sensors_list.append(BJWaterInfoSensor(coordinator, user_code, device_info))

    async_add_entities(sensors_list, False)


# ========== 基类 ==========
class BJWaterBaseSensor(CoordinatorEntity):
    """传感器基类"""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_info: DeviceInfo | None = None) -> None:
        super().__init__(coordinator)
        self._unique_id = None
        if device_info is not None:
            self._attr_device_info = device_info

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def should_poll(self):
        return False


# ========== 数值型传感器 ==========
class BJWaterSensor(BJWaterBaseSensor, SensorEntity):
    """单个数值传感器（阶梯用量、单价等）"""

    def __init__(
        self,
        coordinator,
        user_code,
        sensor_key,
        sensor_value,
        device_info: DeviceInfo | None = None,
        sensor_num=0,
    ) -> None:
        super().__init__(coordinator, device_info)
        if sensor_num == 0:
            self._unique_id = f"{DOMAIN}.{user_code}_{sensor_key}"
        else:
            self._unique_id = f"{DOMAIN}.{user_code}_{sensor_key}_{sensor_num}"
        self.entity_id = self._unique_id
        self.sensor_key = sensor_key
        self.sensor_value = sensor_value
        self.sensor_num = sensor_num

    def get_value(self, attribute=None):
        try:
            if attribute is None:
                return self.sensor_value
            return SENSORS[self.sensor_key]["attribute"]
        except KeyError:
            return STATE_UNKNOWN

    @property
    def name(self):
        name = SENSORS[self.sensor_key]["name"]
        if self.sensor_num > 0:
            name = f"{name}_{self.sensor_num}"
        return name

    @property
    def state(self):
        return self.get_value()

    @property
    def state_class(self):
        return SENSORS[self.sensor_key].get("state_class")

    @property
    def icon(self):
        return SENSORS[self.sensor_key]["icon"]

    @property
    def device_class(self):
        return SENSORS[self.sensor_key].get("device_class")

    @property
    def unit_of_measurement(self):
        return SENSORS[self.sensor_key]["unit_of_measurement"]


# ========== 历史费用传感器 ==========
class BJWaterHistoryFeeSensor(BJWaterBaseSensor):
    """历史缴费记录传感器"""

    def __init__(
        self, coordinator, user_code, bill_date,
        sensor_attrs, index, device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, device_info)
        self._unique_id = f"{DOMAIN}.{user_code}_{index}_Fee"
        self.entity_id = self._unique_id
        self._bill_date = bill_date
        self.sensor_attrs = sensor_attrs

    @property
    def name(self):
        return self._bill_date.replace("-", "") + "_Fee"

    @property
    def state(self):
        return self.sensor_attrs.get("amount", 0)

    @property
    def icon(self):
        return "hass:currency-cny"

    @property
    def unit_of_measurement(self):
        return "CNY"

    @property
    def extra_state_attributes(self):
        attrs = {}
        for k, v in self.sensor_attrs.items():
            name = HISTORY_FEE_SENSORS.get(k, {}).get("name", k)
            if k == "pay":
                attrs[name] = "未缴费" if v == 0 else "已缴费"
            else:
                attrs[name] = v
        return attrs

    @property
    def device_class(self) -> str | None:
        return SensorDeviceClass.WATER


# ========== 历史用量传感器 ==========
class BJWaterHistoryUsageSensor(BJWaterBaseSensor):
    """历史用水量传感器"""

    def __init__(
        self, coordinator, user_code, bill_date,
        sensor_attrs, index, device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, device_info)
        self._unique_id = f"{DOMAIN}.{user_code}_{index}_Usage"
        self.entity_id = self._unique_id
        self._bill_date = bill_date
        self.sensor_attrs = sensor_attrs

    @property
    def name(self):
        return self._bill_date.replace("-", "") + "_Usage"

    @property
    def state(self):
        return self.sensor_attrs.get("usage", 0)

    @property
    def icon(self):
        return "hass:water-circle"

    @property
    def unit_of_measurement(self):
        return "m³"

    @property
    def extra_state_attributes(self):
        attrs = {}
        for k, v in self.sensor_attrs.items():
            if k == "usage":
                attrs[HISTORY_USAGE_SENSORS[k]["name"]] = v
            elif k == "value":
                if isinstance(v, list) and len(v) > 0:
                    value_list = v[0]
                    if isinstance(value_list, list) and len(value_list) > 0:
                        attrs[HISTORY_USAGE_SENSORS[k]["name"]] = value_list[0]
        return attrs

    @property
    def device_class(self) -> str | None:
        return SensorDeviceClass.WATER


# ========== 主信息传感器（兼容 electricity-info-card） ==========
class BJWaterInfoSensor(BJWaterBaseSensor):
    """水费综合信息传感器

    state: 最新月份水费金额
    attributes: daylist / monthlist / yearlist（兼容 electricity-info-card 格式）
    """

    def __init__(
        self, coordinator, user_code, device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, device_info)
        self._unique_id = f"{DOMAIN}.{user_code}_info"
        self.entity_id = self._unique_id
        self._user_code = user_code

    @property
    def name(self):
        return "北京水费信息"

    @property
    def state(self):
        """最新月份的水费金额"""
        data = self.coordinator.data
        monthlist = data.get("monthlist", [])
        if monthlist:
            return monthlist[0].get("monthWaterCost", 0)
        return 0

    @property
    def icon(self):
        return "hass:water"

    @property
    def unit_of_measurement(self):
        return "CNY"

    @property
    def device_class(self) -> str | None:
        return SensorDeviceClass.WATER

    @property
    def extra_state_attributes(self):
        """返回 electricity-info-card 兼容的 daylist/monthlist/yearlist"""
        data = self.coordinator.data
        return {
            "date": data.get("user_code", ""),
            "daylist": data.get("daylist", []),
            "monthlist": data.get("monthlist", []),
            "yearlist": data.get("yearlist", []),
            "user_code": data.get("user_code", ""),
            "total_usage": data.get("total_usage", 0),
            "first_step_left": data.get("first_step_left", 0),
            "second_step_left": data.get("second_step_left", 0),
            "first_step_price": data.get("first_step_price", 0),
            "wastwater_treatment_price": data.get("wastwater_treatment_price", 0),
            "water_tax": data.get("water_tax", 0),
            "total_cost": data.get("total_cost", 0),
        }
