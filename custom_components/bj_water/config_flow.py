"""北京水费配置流程"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bj_water import BJWater, InvalidData
from .const import DOMAIN, LOGGER

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required("userCode"): str})
STEP_REGION_SCHEMA = vol.Schema({vol.Required("region"): vol.In(["beijing"])})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """验证用户输入的户号是否有效"""
    session = async_get_clientsession(hass)
    user_code = data["userCode"]

    if not user_code.isdigit():
        raise InvalidFormat

    api = BJWater(session, user_code)
    try:
        await api.get_bill_cycle_range()
    except InvalidData as exc:
        LOGGER.error("验证失败: %s", exc)
        raise InvalidAuth from exc
    except ClientError as exc:
        LOGGER.error("连接失败: %s", exc)
        raise CannotConnect from exc

    return {"title": f"水表户号: {user_code}"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理 bj_water 的配置流程"""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """配置第一步：选择区域"""
        if user_input is not None:
            # 区域选择完成，进入户号输入步骤
            return await self.async_step_enter_code()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_REGION_SCHEMA,
            description_placeholders={"area": "北京"},
        )

    async def async_step_enter_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """配置第二步：输入户号"""
        errors: dict[str, str] = {}

        if user_input is not None:
            # 检查重复配置
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.data.get("userCode") == user_input["userCode"]:
                    return self.async_abort(reason="already_configured")

            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidFormat:
                errors["base"] = "invalid_format"
            except Exception:
                LOGGER.exception("未知异常")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="enter_code", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """无法连接"""


class InvalidAuth(HomeAssistantError):
    """户号无效"""


class InvalidFormat(HomeAssistantError):
    """格式无效"""
