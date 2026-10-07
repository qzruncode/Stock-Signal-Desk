import React, { useEffect, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
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
  const { register, handleSubmit } = useForm<FormState>({ defaultValues: EMPTY });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<RssItem[]>([]);
  const [feedTitle, setFeedTitle] = useState('');
  // Abort + sequence guard so repeated "预览" clicks (or a click after the modal
  // closes) can't let a slow earlier response overwrite the latest result.
  const previewSeqRef = useRef(0);
  const previewAbortRef = useRef<AbortController | null>(null);

  // Cancel any in-flight preview when the modal closes so a late response can't
  // setState on an unmounted/hidden form.
  useEffect(() => {
    if (isOpen) return;
    previewAbortRef.current?.abort();
  }, [isOpen]);

  const buildRequest = (values: FormState): HtmlTransformRequest => ({
    url: values.url,
    title: values.title || undefined,
    item: values.item || 'html',
    itemTitle: values.itemTitle || undefined,
    itemLink: values.itemLink || undefined,
    itemDesc: values.itemDesc || undefined,
    itemPubDate: values.itemPubDate || undefined,
    itemContent: values.itemContent || undefined,
    encoding: values.encoding || undefined,
    limit: 20,
  });

  const handlePreview = async (values: FormState) => {
    if (!values.url.trim()) {
      setError('请输入目标网页 URL');
      return;
    }
    // Cancel any in-flight preview before starting a new one — a slow earlier
    // transform must not overwrite this one's result.
    previewAbortRef.current?.abort();
    const ctrl = new AbortController();
    previewAbortRef.current = ctrl;
    const seq = ++previewSeqRef.current;

    setLoading(true);
    setError(null);
    setItems([]);
    try {
      const res = await rssApi.transformHtml(buildRequest(values), ctrl.signal);
      if (seq !== previewSeqRef.current) return; // superseded — don't touch state
      setItems(res.items || []);
      setFeedTitle(res.feed_title || '');
      if (res.errors?.length && !res.items?.length) setError(res.errors.join('; '));
    } catch (err) {
      if (seq !== previewSeqRef.current) return; // superseded — don't touch state
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if ((err as { code?: string })?.code === 'ERR_CANCELED') return;
      setError((err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error).message || '转换失败');
    } finally {
      if (seq === previewSeqRef.current) setLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="网页转 RSS（HTML→RSS 万能转换器）" width="max-w-3xl">
      <form onSubmit={handleSubmit(handlePreview)} className="space-y-3">
        <p className="text-xs text-muted-text">
          把任意网页转成 RSS。填入目标 URL 与 CSS 选择器（RSSHub 会按选择器提取条目）。需 RSSHub 实例开启 <code className="text-secondary-text">ALLOW_USER_SUPPLY_UNSAFE_DOMAIN</code>。
        </p>

        <Input label="目标网页 URL *" placeholder="https://example.com/news" {...register('url')} />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Input label="Feed 标题（可选）" placeholder="默认取页面标题" {...register('title')} />
          <Input label="item 选择器 *" placeholder="如 div.post" {...register('item')} />
          <Input label="标题选择器 itemTitle" placeholder="如 h2 a" {...register('itemTitle')} />
          <Input label="链接选择器 itemLink" placeholder="如 a" {...register('itemLink')} />
          <Input label="描述选择器 itemDesc" placeholder="如 p.summary" {...register('itemDesc')} />
          <Input label="时间选择器 itemPubDate" placeholder="如 time" {...register('itemPubDate')} />
          <Input label="正文选择器 itemContent" placeholder="二次抓取完整正文" {...register('itemContent')} />
          <Input label="编码 encoding" placeholder="默认 utf-8" {...register('encoding')} />
        </div>

        <div className="flex gap-2">
          <Button type="submit" variant="primary" size="sm" isLoading={loading}>
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
      </form>
    </Modal>
  );
};
