import type { ToolMeta, ToolParameterSpec } from '../types/toolRegistry';

/** 判断参数类型是否为数值(integer/number)。 */
export function isNumericParam(param: ToolParameterSpec): boolean {
  return param.type === 'integer' || param.type === 'number';
}

/**
 * 取参数的初始表单值(字符串形式,便于受控 input)。
 * 优先用 default;enum 取第一个候选;boolean 取 'false';数值取空串。
 */
export function initialParamValue(param: ToolParameterSpec): string {
  if (param.default !== undefined && param.default !== null && param.default !== '') {
    return String(param.default);
  }
  if (Array.isArray(param.enum) && param.enum.length > 0) {
    return String(param.enum[0]);
  }
  if (param.type === 'boolean') {
    return 'false';
  }
  return '';
}

/**
 * 把表单字符串值集合转成后端可执行的 arguments 对象。
 * - 空字符串的可选参数直接丢弃(不提交);
 * - 数值参数转 Number(空串跳过);
 * - boolean 参数转 bool;
 * - 其余按字符串提交。
 * 返回 { args, missingRequired } —— missingRequired 列出未填的必填参数名,
 * 由调用方做前端弱校验(非空)。
 */
export function buildArguments(
  tool: ToolMeta,
  values: Record<string, string>,
): { args: Record<string, unknown>; missingRequired: string[] } {
  const args: Record<string, unknown> = {};
  const missingRequired: string[] = [];

  for (const param of tool.parameters) {
    const raw = (values[param.name] ?? '').trim();

    if (raw === '') {
      if (param.required) {
        missingRequired.push(param.name);
      }
      // 空值(含可选参数未填)一律不提交,让后端用 executor 默认值
      continue;
    }

    if (param.type === 'boolean') {
      args[param.name] = raw === 'true' || raw === '1';
    } else if (isNumericParam(param)) {
      const num = Number(raw);
      args[param.name] = Number.isNaN(num) ? raw : num;
    } else {
      args[param.name] = raw;
    }
  }

  return { args, missingRequired };
}
