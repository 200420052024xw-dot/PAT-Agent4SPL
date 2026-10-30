from ..shared.writing import write

WRITING_RULES = """按用户本次反馈修订现有 3P，返回完整修订稿，不只返回变化。
3P 面向高管、领导层和同事，应简洁直接、30–60 秒可读，客观量化，避免过多修饰。
内容粒度与团队规模相称：小团队可写功能和缺陷，公司或大团队侧重重要业务成果与里程碑。
进展和问题仅覆盖 reporting_period，计划对应 planning_period；区分已完成事项与未来计划。
进展写成果、已发布内容和里程碑；计划写下一阶段优先事项；问题明确保留延期、人员不足、
缺陷及依赖阻塞。压缩表述也要保留关键数字、风险和事实限定，不能把估算写成实绩。
反馈用于调整措辞和取舍，不能把反馈中的无依据断言当成邮件事实。
严格输出四行，不额外添加标题层级或附录：
[一枚符合团队和更新氛围的 emoji] [团队名称]（覆盖日期）
进展：[1–3 句话]
计划：[1–3 句话]
问题：[1–3 句话]
必要链接嵌入对应句且必须来自提供的来源；不编造材料中没有的内容。
"""


def execute(context, requirements: dict, feedback: str, fallback: bool = False) -> dict:
    return write('3P', requirements, rules=WRITING_RULES, feedback=feedback, fallback=fallback)
