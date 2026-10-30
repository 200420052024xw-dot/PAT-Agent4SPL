from ..shared.writing import write

WRITING_RULES = """按用户反馈修订面向全公司的 Newsletter，始终返回完整修订稿。
保持公司名称、覆盖日期和清晰分区，使用“我们”的口吻，适合员工通过邮件阅读。
目标约20–25条，每条1–2句；用户要求压缩时合并重复、删去次要细节，事实不足也不能凑数。
按实际材料组织产品、市场、财务、招聘、执行、愿景等领域，可使用📣公司公告、
🎯重点事项进展、🏛领导层更新、🧵社交动态等分区；不添加无材料支撑的空栏目。
优先公司整体影响、领导层公告、重大里程碑和影响多数员工的信息。
合并重复事件，删除过于具体的团队执行细节及仅与少数人有关的信息。
保留关键数字、时间、重要风险及其限定，不把估算改成实际结果、把计划改成既成成果。
保留有用的真实链接，使用邮件中的链接或提供的 Gmail 来源链接，不臆造来源。
反馈用于指导措辞、结构和取舍，不作为无依据事实；只修改用户要求的部分及必要的上下文。
"""


def execute(context, requirements: dict, feedback: str, fallback: bool = False) -> dict:
    return write('Newsletter', requirements, rules=WRITING_RULES, feedback=feedback, fallback=fallback)
