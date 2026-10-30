from ..shared.writing import write

WRITING_RULES = """撰写 3P 更新（进展、计划、问题），受众是高管、领导层和同事。
内容简洁直接，30–60 秒可读，客观量化，避免修饰性语言。根据团队规模选择事实粒度：
小团队可写功能和缺陷，大团队或公司应写更重要的成果、人员和业务里程碑。
进展和问题覆盖 reporting_period；计划覆盖 planning_period。按事项发生/计划时间归类，
不要把邮件发送日期等同于事项日期，不把尚未完成的计划写成已完成成果。
进展重点写已发布内容、成果和里程碑；计划重点写下一阶段优先事项；问题明确写出
人员缺口、缺陷、延期、依赖和其他阻塞。保留重要数字，不虚构事实或凑字数。
严格输出四行，不增加章节、说明或独立来源附录：
[一枚符合团队和更新氛围的 emoji] [团队名称]（覆盖日期）
进展：[1–3 句话]
计划：[1–3 句话]
问题：[1–3 句话]
如需来源链接，将已提供的真实链接嵌入对应句中。资料未涉及的部分明确说明缺少材料。
"""


def execute(context, requirements: dict, fallback: bool = False) -> dict:
    return write('3P', requirements, rules=WRITING_RULES, fallback=fallback)
