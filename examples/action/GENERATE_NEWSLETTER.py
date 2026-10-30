from ..shared.writing import write

WRITING_RULES = """撰写面向全公司的 Newsletter，汇总 reporting_period 内的重要信息。
采用“我们”的口吻，适合全公司员工通过邮件阅读。标题包含公司和覆盖日期。
目标约20–25个要点，每条1–2句话，简短直接；资料不足时减少条目，不编造或重复凑数。
将相似信息归入清晰分区，使有材料支持的不同公司领域得到体现，例如产品开发、市场拓展、
财务、招聘、执行及愿景。可按材料采用以下版式，不要求硬凑空分区：
📣 公司公告
- 简短公告及真实链接
🎯 重点事项进展
- 按领域组织的重要成果
🏛 领导层更新
- 领导层的重要消息
🧵 社交动态
- 与员工普遍相关的动态
优先全公司影响、领导层公告、重要里程碑和影响大多数员工的信息。
剔除过细的团队执行事项、只与少数人有关的内容，合并同一事件的重复邮件和重复表述。
保留关键数字、时间与事实限定；预计值不是已实现值，计划不是已完成成果。
尽量保留有用链接，引用邮件中的真实链接或提供的 Gmail 来源链接，不臆造 URL。
只写有资料支持的事实，不因篇幅目标扩大或添加成果。
"""


def execute(context, requirements: dict, fallback: bool = False) -> dict:
    return write('Newsletter', requirements, rules=WRITING_RULES, fallback=fallback)
