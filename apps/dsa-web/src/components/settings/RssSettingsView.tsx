import React, { useState } from 'react';
import { ChevronDown, ListFilter, Wand2 } from 'lucide-react';
import { Button } from '../common';
import { HtmlTransformerForm } from '../rss/HtmlTransformerForm';
import { RssExplorePanel } from '../rss/RssExplorePanel';
import { useRssNamespaces } from '../../hooks/useRssNamespaces';

const RssSettingsView: React.FC = () => {
  const [transformerOpen, setTransformerOpen] = useState(false);
  const [sourceListOpen, setSourceListOpen] = useState(false);
  const {
    loading,
    error,
    filtered,
  } = useRssNamespaces();

  return (
    <section className="space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">RSS 数据源</h2>
          <p className="mt-1 text-sm text-secondary-text">
            浏览已筛选的财经数据源，配置路由参数并预览、导出 Feed 内容。
          </p>
        </div>
        <div className="flex gap-2 sm:shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="flex-1 sm:flex-none"
            onClick={() => setTransformerOpen(true)}
          >
            <Wand2 className="h-3.5 w-3.5" />
            网页转 RSS
          </Button>
          <Button
            variant={sourceListOpen ? 'secondary' : 'outline'}
            size="sm"
            className="flex-1 sm:flex-none"
            aria-controls="rss-source-list"
            aria-expanded={sourceListOpen}
            onClick={() => setSourceListOpen((open) => !open)}
          >
            <ListFilter className="h-3.5 w-3.5" />
            {sourceListOpen ? '收起列表' : `数据源列表${filtered.length ? `（${filtered.length}）` : ''}`}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${sourceListOpen ? 'rotate-180' : ''}`} />
          </Button>
        </div>
      </header>

      <div className="min-h-0 lg:h-[calc(100dvh-11rem)] lg:min-h-[42rem]">
        <RssExplorePanel
          loading={loading}
          error={error}
          filtered={filtered}
          sourceListOpen={sourceListOpen}
        />
      </div>

      <HtmlTransformerForm
        isOpen={transformerOpen}
        onClose={() => setTransformerOpen(false)}
      />
    </section>
  );
};

export default RssSettingsView;
