import { Input, Select } from '../common';
import { isNumericParam } from '../../utils/toolTestParams';
import type { ToolParameterSpec } from '../../types/toolRegistry';

interface ToolParamInputProps {
  param: ToolParameterSpec;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

/**
 * 单个工具参数的输入控件。无 label(参数名/类型/必填已在父列表行展示),
 * 仅渲染一个适配类型的受控输入:enum→Select、boolean→Select、数值→number Input、其余→text Input。
 */
export const ToolParamInput: React.FC<ToolParamInputProps> = ({
  param,
  value,
  onChange,
  disabled,
}) => {
  if (Array.isArray(param.enum) && param.enum.length > 0) {
    return (
      <Select
        value={value}
        onChange={onChange}
        disabled={disabled}
        options={param.enum.map((opt) => ({ value: String(opt), label: String(opt) }))}
      />
    );
  }

  if (param.type === 'boolean') {
    return (
      <Select
        value={value}
        onChange={onChange}
        disabled={disabled}
        options={[
          { value: 'true', label: 'true' },
          { value: 'false', label: 'false' },
        ]}
      />
    );
  }

  return (
    <Input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      type={isNumericParam(param) ? 'number' : 'text'}
      disabled={disabled}
    />
  );
};

export default ToolParamInput;
