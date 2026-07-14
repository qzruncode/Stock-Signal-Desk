import { useEffect } from 'react';
import { ArrowLeft, Hammer } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ToolRegistryView } from '../components/tools/ToolRegistryView';

const ToolsPage: React.FC = () => {
  useEffect(() => {
    document.title = 'Tool 集合 - Stock Assistant';
  }, []);

  return (
    <div className="mx-auto max-w-6xl space-y-6 py-6">
      <header>
        <Link
          to="/"
          className="mb-3 inline-flex items-center gap-1 text-sm text-secondary-text transition hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回首页
        </Link>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-foreground">
          <Hammer className="h-6 w-6 text-primary" />
          Tool 集合
        </h1>
      </header>

      <main className="min-w-0">
        <ToolRegistryView />
      </main>
    </div>
  );
};

export default ToolsPage;
