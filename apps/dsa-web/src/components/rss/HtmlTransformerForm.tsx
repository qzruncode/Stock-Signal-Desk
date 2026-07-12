import React, { useState } from 'react';
import { Wand2 } from 'lucide-react';
import type { HtmlTransformRequest, RssItem } from '../../api/rss';
import { Modal, Button, Input, InlineAlert, Loading, EmptyState } from '../common';
import { rssApi } from '../../api/rss';
import { RssFeedList } from './RssFeedList';

export interface HtmlTransformerFormProps {
  isOpen: boolean;
  onClose: () => void;
}

interface FormState {
  url: string;
  title: string;
  item: string;
  itemTitle: string;
  itemLink: string;
  itemDesc: string;
  itemPubDate: string;
  itemContent: string;
  encoding: string;
}

const EMPTY: FormState = {
  url: '', title: '', item: 'html', itemTitle: '', itemLink: '', itemDesc: '',
  itemPubDate: '', itemContent: '', encoding: '',
};

export const HtmlTransformerForm: React.FC<HtmlTransformerFormProps> = ({ isOpen, onClose }) => {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<RssItem[]>([]);
  const [feedTitle, setFeedTitle] = useState('');

  const update = (k: keyof FormState, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const buildRequest = (): HtmlTransformRequest => ({
    url: form.url,
    title: form.title || undefined,
    item: form.item || 'html',
    itemTitle: form.itemTitle || undefined,
    itemLink: form.itemLink || undefined,
    itemDesc: form.itemDesc || undefined,
    itemPubDate: form.itemPubDate || undefined,
    itemContent: form.itemContent || undefined,
    encoding: form.encoding || undefined,
    limit: 20,
  });

  const handlePreview = async () => {
    if (!form.url.trim()) {
      setError('请输入目标网页 URL');
      return;
    }
    setLoading(true);
    setError(null);
    setItems([]);
    try {
      const res = await rssApi.transformHtml(buildRequest());
      setItems(res.items || []);
      setFeedTitle(res.feed_title || '');
      if (res.errors?.length && !res.items?.length) setError(res.errors.join('; '));
    } catch (err) {
      setError((err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error).message || '转换失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="网页转 RSS（HTML→RSS 万能转换器）" width="max-w-3xl">
      <div className="space-y-3">
        <p className="text-xs text-muted-text">
          把任意网页转成 RSS。填入目标 URL 与 CSS 选择器（RSSHub 会按选择器提取条目）。需 RSSHub 实例开启 <code className="text-secondary-text">ALLOW_USER_SUPPLY_UNSAFE_DOMAIN</code>。
        </p>

        <Input label="目标网页 URL *" placeholder="https://example.com/news" value={form.url} onChange={(e) => update('url', e.target.value)} />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Input label="Feed 标题（可选）" placeholder="默认取页面标题" value={form.title} onChange={(e) => update('title', e.target.value)} />
          <Input label="item 选择器 *" placeholder="如 div.post" value={form.item} onChange={(e) => update('item', e.target.value)} />
          <Input label="标题选择器 itemTitle" placeholder="如 h2 a" value={form.itemTitle} onChange={(e) => update('itemTitle', e.target.value)} />
          <Input label="链接选择器 itemLink" placeholder="如 a" value={form.itemLink} onChange={(e) => update('itemLink', e.target.value)} />
          <Input label="描述选择器 itemDesc" placeholder="如 p.summary" value={form.itemDesc} onChange={(e) => update('itemDesc', e.target.value)} />
          <Input label="时间选择器 itemPubDate" placeholder="如 time" value={form.itemPubDate} onChange={(e) => update('itemPubDate', e.target.value)} />
          <Input label="正文选择器 itemContent" placeholder="二次抓取完整正文" value={form.itemContent} onChange={(e) => update('itemContent', e.target.value)} />
          <Input label="编码 encoding" placeholder="默认 utf-8" value={form.encoding} onChange={(e) => update('encoding', e.target.value)} />
        </div>

        <div className="flex gap-2">
          <Button variant="primary" size="sm" isLoading={loading} onClick={() => void handlePreview()}>
            <Wand2 className="h-3.5 w-3.5" />
            预览
          </Button>
        </div>

        {error && <InlineAlert title="转换失败" variant="danger" message={error} />}
        {loading && <Loading label="正在转换…" />}
        {!loading && items.length > 0 && <RssFeedList items={items} feedTitle={feedTitle} />}
        {!loading && !error && items.length === 0 && (
          <EmptyState title="填写参数后点击预览" description="预览成功后会在此显示转换后的 RSS 内容。" />
        )}
      </div>
    </Modal>
  );
};

export default HtmlTransformerForm;
