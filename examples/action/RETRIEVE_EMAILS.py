"""Retrieve real Gmail messages using the package's OAuth files."""
from ..shared.gmail import GmailClient, fingerprint, query_for
from ..shared.requirements import validate_requirements
from requests import RequestException


def execute(context, requirements: dict) -> dict:
    client = None
    try:
        validate_requirements(requirements)
        if requirements.get('use_user_material') and requirements.get('user_material', '').strip():
            return {"execution_status": "success", "result": [],
                    "reason": "用户已选择使用其提供的材料。"}
        fields = {key: requirements.get(key, '') for key in
                  ('comms_type', 'scope_name', 'reporting_period', 'planning_period', 'senders', 'keywords')}
        stamp = fingerprint(**fields)
        query = query_for(**{key: value for key, value in fields.items() if key != 'scope_name'})
        client = GmailClient()
        evidence = client.retrieve(query, stamp)
        if not evidence:
            return {"execution_status": "failure", "reason": f"Gmail 未找到匹配邮件。查询：{query}。请澄清筛选条件、重试或由用户提供材料；不能编造证据。"}
        return {"execution_status": "success", "result": evidence}
    except (ValueError, OSError, RuntimeError, RequestException) as exc:
        return {"execution_status": "failure", "reason": str(exc)}
    finally:
        if client:
            client.close()
