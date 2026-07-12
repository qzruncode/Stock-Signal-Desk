import React, { useState } from 'react';
import { Wand2 } from 'lucide-react';
import { Button } from '../components/common';
import { useRssNamespaces } from '../hooks/useRssNamespaces';
import { RssExplorePanel } from '../components/rss/RssExplorePanel';
import { HtmlTransformerForm } from '../components/rss/HtmlTransformerForm';

const RssPage: React.FC = () => {
  const [transformerOpen, setTransformerOpen] = useState(false);
  const namespaces = useRssNamespaces();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="shrink-0 bg-muted/30 px-6">
        <div className="flex items-center justify-end gap-3 py-2">
          <Button variant="outline" size="sm" onClick={() => setTransformerOpen(true)}>
            <Wand2 className="h-3.5 w-3.5" />
            网页转 RSS
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        <div className="h-full min-h-0">
          <RssExplorePanel
            routes={namespaces.routes}
            loading={namespaces.loading}
            error={namespaces.error}
            search={namespaces.search}
            setSearch={namespaces.setSearch}
            filtered={namespaces.filtered}
            onReload={() => void namespaces.reload()}
            categories={namespaces.categories}
            category={namespaces.category}
            setCategory={namespaces.setCategory}
            stale={namespaces.stale}
          />
        </div>
      </div>

      <HtmlTransformerForm isOpen={transformerOpen} onClose={() => setTransformerOpen(false)} />
    </div>
  );
};

export default RssPage;
