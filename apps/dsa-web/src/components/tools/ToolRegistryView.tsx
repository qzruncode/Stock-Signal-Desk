import { Loader2, RotateCcw } from 'lucide-react';
import { Button, InlineAlert } from '../common';
import { ToolListItem } from './ToolListItem';
import { useToolRegistry } from '../../hooks/useToolRegistry';

export const ToolRegistryView: React.FC = () => {
  const { status, data, error, refetch } = useToolRegistry();

  if (status === 'loading') {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-cyan" />
          <p className="text-sm text-secondary-text">加载工具列表...</p>
        </div>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="space-y-4">
        <InlineAlert
          variant="danger"
          title="加载工具列表失败"
          message={error ?? '未知错误'}
        />
        <Button variant="secondary" onClick={refetch}>
          <RotateCcw className="h-4 w-4" />
          重试
        </Button>
      </div>
    );
  }

  const tools = data?.tools ?? [];

  return (
    <section className="space-y-2">
      {tools.map((tool) => (
        <ToolListItem key={tool.name} tool={tool} />
      ))}
    </section>
  );
};

export default ToolRegistryView;
