"""Input types shared by collection and direct slot revision."""
from .gmail import period

ORDER = ("comms_type", "scope_name", "reporting_period", "planning_period", "senders", "keywords")
QUESTIONS = {
    "comms_type": "你要撰写 3P 更新，还是公司 Newsletter？",
    "scope_name": "这份内部沟通面向哪个团队或公司？",
    "reporting_period": "这次内容覆盖哪个时间段？请提供开始日期和结束日期。",
    "planning_period": "计划部分覆盖哪个时间段？请提供开始日期和结束日期。",
    "senders": "检索 Gmail 时，需要关注哪些发件人？请提供姓名或邮箱地址。",
    "keywords": "检索邮件时，需要使用哪些关键词？",
}


def validate_slot(name, value):
    if name == "comms_type":
        if value not in ("3P", "Newsletter"):
            raise ValueError("comms_type 只能是 3P 或 Newsletter")
    elif name in ("reporting_period", "planning_period"):
        if not isinstance(value, str):
            raise ValueError("日期范围必须是字符串")
        period(value)
    elif name in ("senders", "keywords"):
        if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
            raise ValueError(f"{name} 必须是非空字符串列表")
        value = list(dict.fromkeys(x.strip() for x in value))
    elif name == "use_user_material":
        if type(value) is not bool:
            raise ValueError("use_user_material 必须是 boolean")
    elif name in ("scope_name", "user_material"):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不得为空")
        value = value.strip()
    else:
        raise ValueError(f"不可修改的需求槽位：{name}")
    return value


def validate_requirements(slots):
    for name in ORDER:
        if name == 'planning_period' and slots.get('comms_type') != '3P':
            continue
        validate_slot(name, slots.get(name))
